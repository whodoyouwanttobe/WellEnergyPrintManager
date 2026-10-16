"""Состояние принтеров: SNMP (чернила по цветам, замятие, бумага), очередь Windows, занятость."""
import socket
import threading

import pytest

from core import printer_status as ps
from core import snmp


# ───────────── поддельный принтер (SNMP-агент на localhost) ─────────────
class FakeAgent:
    def __init__(self, mib):
        self.mib = dict(mib)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self.running = True
        self.requests = 0
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _sorted(self):
        return sorted(self.mib, key=snmp.oid_tuple)

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self.requests += 1
            v, c, t, rid, *_rest, vbs = snmp.parse_message(data)
            out = []
            for oid, _ in vbs:
                if t == snmp.GET:
                    out.append((oid, self.mib.get(oid, snmp.END)))
                else:
                    nxt = next((o for o in self._sorted() if snmp.oid_tuple(o) > snmp.oid_tuple(oid)), None)
                    out.append((nxt or oid, self.mib[nxt] if nxt else snmp.END))
            self.sock.sendto(snmp.build_response(rid, out, c, v), addr)

    def stop(self):
        self.running = False
        self.sock.close()


def canon_mib(errors=b"\x00\x00", printer_status=3, cyan=0, alerts=()):
    m = {
        f"{ps.HR_PRINTER_STATUS}.1": printer_status,
        f"{ps.HR_PRINTER_ERRORS}.1": errors,
        f"{ps.HR_DEVICE_STATUS}.1": 2,
        f"{ps.IN_LEVEL}.1.1": -3, f"{ps.IN_MAX}.1.1": -2, f"{ps.IN_DESCR}.1.1": b"Roll 1",
    }
    for i, (descr, lvl) in enumerate([(b"Matte Black", 80), (b"Black", 50), (b"Cyan", cyan),
                                      (b"Magenta", 7), (b"Yellow", 60), (b"Maintenance Cartridge", 30)], start=1):
        m[f"{ps.SUP_DESCR}.1.{i}"] = descr
        m[f"{ps.SUP_MAX}.1.{i}"] = 100
        m[f"{ps.SUP_LEVEL}.1.{i}"] = lvl
        m[f"{ps.SUP_CLASS}.1.{i}"] = 3 if b"Maint" in descr else 4
    for i, txt in enumerate(alerts, start=1):
        m[f"{ps.ALERT_DESCR}.1.{i}"] = txt.encode("utf-8")
        m[f"{ps.ALERT_SEVERITY}.1.{i}"] = 3
    return m


@pytest.fixture
def agent():
    agents = []

    def make(mib):
        a = FakeAgent(mib)
        agents.append(a)
        return a
    yield make
    for a in agents:
        a.stop()


def test_ber_roundtrip():
    pkt, rid = snmp.build_request(snmp.GETNEXT, ["1.3.6.1.2.1.43.11.1.1.9"], "public")
    v, c, t, r, e, i, vbs = snmp.parse_message(pkt)
    assert (v, c, t, r) == (0, "public", snmp.GETNEXT, rid) and vbs[0][0] == "1.3.6.1.2.1.43.11.1.1.9"
    resp = snmp.build_response(rid, [("1.3.6.1.2.1.43.11.1.1.9.1.3", -3), ("1.3.6.1.2.1.43.11.1.1.6.1.1", b"Cyan"),
                                     ("1.3.6.1.4.1.11.2.3.9.4.2.1.1.1.1.200000", 123456)])
    vbs = snmp.parse_message(resp)[6]
    assert vbs == [("1.3.6.1.2.1.43.11.1.1.9.1.3", -3), ("1.3.6.1.2.1.43.11.1.1.6.1.1", b"Cyan"),
                   ("1.3.6.1.4.1.11.2.3.9.4.2.1.1.1.1.200000", 123456)]


def test_walk_table_reads_supplies(agent):
    a = agent(canon_mib())
    c = snmp.SnmpClient("127.0.0.1", port=a.port, timeout=0.5)
    rows = c.walk_table([ps.SUP_DESCR, ps.SUP_LEVEL])
    assert rows["1.3"][ps.SUP_DESCR] == b"Cyan" and rows["1.3"][ps.SUP_LEVEL] == 0
    assert len(rows) == 6


def test_read_snmp_plotter_names_missing_ink(agent, monkeypatch):
    from printing import printer_probe
    a = agent(canon_mib(cyan=0))
    real = snmp.SnmpClient
    monkeypatch.setattr(printer_probe, "SnmpClient", lambda host, **kw: real(host, port=a.port, **kw))
    data = printer_probe.read_snmp("127.0.0.1")
    st = ps.combine({"status": 0, "attributes": 0, "jobs": []}, data, None, plotter=True)
    assert st["text"] == "Нет чернил: Голубые (C)" and st["color"] == "🔴"
    assert "Пурпурные (M) 7%" in st["problems"]["low_ink"]
    assert any("Матовые чёрные (MBK): 80%" in d for d in st["details"])
    assert any("Картридж обслуживания: заполнен на 30%" in d for d in st["details"])


def test_jam_from_snmp_errors_and_printer_alert_text(agent, monkeypatch):
    from printing import printer_probe
    a = agent(canon_mib(cyan=40, errors=bytes([0x04, 0x00]), alerts=["Замятие бумаги в лотке 2"]))
    real = snmp.SnmpClient
    monkeypatch.setattr(printer_probe, "SnmpClient", lambda host, **kw: real(host, port=a.port, **kw))
    st = ps.combine({"status": 0, "jobs": []}, printer_probe.read_snmp("127.0.0.1"), None)
    assert st["text"].startswith("Замятие бумаги") and st["color"] == "🔴"
    assert any("Замятие бумаги в лотке 2" in d for d in st["details"])




def test_hr_error_bits():
    assert ps.decode_hr_errors(bytes([0x40])) == {"no_paper"}           # бит 1
    assert ps.decode_hr_errors(bytes([0x04])) == {"jam"}                # бит 5
    assert ps.decode_hr_errors(bytes([0x10])) == {"no_ink"}             # бит 3
    assert ps.decode_hr_errors(bytes([0x08])) == {"door_open"}          # бит 4
    assert ps.decode_hr_errors(bytes([0xC0])) == {"no_paper"}           # «мало» скрыто «нет»
    assert ps.decode_hr_errors(bytes([0x00, 0x04])) == {"no_paper"}     # бит 13: лоток пуст


def test_spooler_driver_text_and_bits():
    probs, busy, texts = ps.decode_spooler(0, 0, [{"Status": 0x12, "pStatus": "Замятие бумаги"}])
    assert "jam" in probs and busy == "Печатает" and texts == ["Замятие бумаги"]
    probs, busy, _ = ps.decode_spooler(ps.PS["paper_out"])
    assert probs == {"no_paper"}
    probs, _, _ = ps.decode_spooler(0, ps.PRINTER_ATTRIBUTE_WORK_OFFLINE)
    assert probs == {"offline"}


def test_busy_from_tracker_when_windows_says_ready():
    """Windows отдала задание принтеру и пишет «Готов», а он ещё печатает."""
    tracker = {"idle": False, "current": {"fmt": "A1"}, "seconds_left": 240}
    st = ps.combine({"status": 0, "attributes": 0, "jobs": []}, None, tracker)
    assert st["color"] == "🔵" and st["text"] == "Печатает A1 · ≈4 мин"
    st = ps.combine({"status": 0, "attributes": 0, "jobs": []}, None, {"idle": True})
    assert st["text"] == "Готов"


def test_snmp_printing_status_means_busy():
    st = ps.combine({"status": 0, "jobs": []}, {"printer_status": 4, "errors": b""}, None)
    assert st["text"] == "Печатает"
    st = ps.combine({"status": 0, "jobs": []}, {"printer_status": 5, "errors": b""}, None)
    assert st["text"] == "Прогрев"


def test_low_ink_does_not_hide_printing():
    snmp_data = {"printer_status": 4, "errors": b"",
                 "supplies": [{"name": "Голубые (C)", "pct": 5, "state": "low", "waste": False, "raw": "Cyan"}]}
    st = ps.combine({"status": 0, "jobs": []}, snmp_data, None)
    assert st["color"] == "🔵" and "Печатает" in st["text"] and "Мало" in st["text"]


def test_problem_diff_for_notifications():
    assert ps.new_problems({}, {"jam": "", "low_ink": "C 5%"}) == [("jam", ""), ("low_ink", "C 5%")]
    assert ps.new_problems({"jam": ""}, {"jam": ""}) == []
    assert ps.new_problems({"no_ink": "Голубые (C)"}, {"no_ink": "Голубые (C), Жёлтые (Y)"})
    assert ps.resolved_problems({"jam": "", "low_ink": "x"}, {}) == ["jam"]


def test_supply_names():
    assert ps.supply_name(b"PFI-120 MBK Matte Black") == "Матовые чёрные (MBK)"
    assert ps.supply_name(b"Black Cartridge HP W1335A") == "Чёрные (BK)"
    assert ps.supply_name(b"Maintenance Cartridge MC-31") == "Картридж обслуживания"
    assert ps.supply_name(b"\xd0\x93\xd0\xbe\xd0\xbb\xd1\x83\xd0\xb1\xd0\xbe\xd0\xb9") == "Голубые (C)"   # «Голубой»


def test_host_from_port():
    from printing.printer_probe import host_from_port
    assert host_from_port("IP_192.168.1.20") == "192.168.1.20"
    assert host_from_port("192.168.1.21_1") == "192.168.1.21"
    assert host_from_port("USB001") is None and host_from_port("WSD-1234") is None


def test_no_response_returns_none():
    from printing.printer_probe import read_snmp
    from printing import printer_probe
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))      # порт есть, но никто не отвечает
    port = s.getsockname()[1]
    real = snmp.SnmpClient
    try:
        printer_probe.SnmpClient = lambda host, **kw: real(host, port=port, **kw)
        assert read_snmp("127.0.0.1", timeout=0.2) is None
    finally:
        printer_probe.SnmpClient = real
        s.close()


def test_details_most_severe_first():
    snmp_data = {"printer_status": 3, "errors": b"", "supplies": [
        {"name": "Голубые (C)", "pct": 0, "state": "empty", "waste": False, "raw": ""},
        {"name": "Пурпурные (M)", "pct": 6, "state": "low", "waste": False, "raw": ""}]}
    d = ps.combine({"status": 0, "jobs": []}, snmp_data, None)["details"]
    assert d[0].startswith("🔴 Нет чернил") and "Мало" in d[1]


def test_driver_text_used_as_detail():
    st = ps.combine({"status": ps.PS["paper_out"], "jobs": [{"Status": 0x40, "pStatus": "Загрузите бумагу в лоток 2"}]})
    assert st["text"] == "Нет бумаги: Загрузите бумагу в лоток 2"
