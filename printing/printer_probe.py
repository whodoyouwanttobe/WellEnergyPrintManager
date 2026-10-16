"""Опрос принтера: очередь Windows + SNMP самого принтера (если доступен по сети).

IP принтера берём из порта Windows (Standard TCP/IP Port, WSD) или из настроек
(«Настройки → Система → IP принтера/плоттера»), если автоматически не определился.
"""
import ipaddress
import logging
import re
import socket
import time

from core import printer_status as ps
from core.snmp import SnmpClient, SnmpError

log = logging.getLogger(__name__)
_IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")


def _valid_ip(s):
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


def host_from_port(port_name):
    """«192.168.1.20», «IP_192.168.1.20», «192.168.1.20_1», «PLOTTER01:» → хост или None."""
    p = (port_name or "").strip().rstrip(":")
    if not p or p.upper().startswith(("USB", "LPT", "COM", "FILE", "PORTPROMPT", "NUL", "XPS", "WSD-")):
        return None
    m = _IP_RE.search(p)
    if m and _valid_ip(m.group(1)):
        return m.group(1)
    return None


def _reg_value(root, path, name):
    import winreg
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def host_from_registry(port_name, printer_name):
    """Standard TCP/IP Port → HostName/IPAddress; WSD → LocationInformation устройства."""
    try:
        import winreg
    except ImportError:
        return None
    base = r"SYSTEM\CurrentControlSet\Control\Print\Monitors\Standard TCP/IP Port\Ports" + "\\" + (port_name or "")
    for name in ("HostName", "IPAddress"):
        v = _reg_value(winreg.HKEY_LOCAL_MACHINE, base, name)
        if v:
            return str(v).strip()
    # WSD: ищем устройство с таким же именем и адресом в LocationInformation
    try:
        root = r"SYSTEM\CurrentControlSet\Enum\SWD\PRINTENUM"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as k:
            for i in range(512):
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                path = root + "\\" + sub
                fn = _reg_value(winreg.HKEY_LOCAL_MACHINE, path, "FriendlyName") or ""
                if printer_name and fn and (fn == printer_name or fn in printer_name or printer_name in fn):
                    loc = _reg_value(winreg.HKEY_LOCAL_MACHINE, path, "LocationInformation") or ""
                    m = _IP_RE.search(str(loc))
                    if m:
                        return m.group(1)
    except OSError:
        pass
    return None


def read_spooler(printer_name):
    """→ {"status", "attributes", "jobs", "port", "server"} или исключение."""
    import win32print
    h = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h, 2)
        try:
            jobs = list(win32print.EnumJobs(h, 0, 64, 2) or [])
        except Exception:
            jobs = []
        return {"status": info.get("Status", 0), "attributes": info.get("Attributes", 0),
                "jobs": jobs, "port": info.get("pPortName", ""), "server": info.get("pServerName") or ""}
    finally:
        win32print.ClosePrinter(h)


def read_snmp(host, community="public", timeout=0.8):
    """Всё нужное из Printer MIB за ~10–30 коротких UDP-запросов. → dict или None."""
    c = SnmpClient(host, community=community, timeout=timeout, retries=0)
    try:
        hr = c.walk_table([ps.HR_PRINTER_STATUS, ps.HR_PRINTER_ERRORS], max_rows=4)
    except SnmpError as e:
        log.debug("SNMP %s: %s", host, e)
        return None
    out = {"errors": b"", "printer_status": None, "device_status": None,
           "supplies": [], "inputs": [], "alerts": [], "console": ""}
    for idx, r in hr.items():
        out["printer_status"] = r.get(ps.HR_PRINTER_STATUS, out["printer_status"])
        e = r.get(ps.HR_PRINTER_ERRORS)
        if isinstance(e, (bytes, bytearray)):
            out["errors"] = e
        try:
            out["device_status"] = c.get(f"{ps.HR_DEVICE_STATUS}.{idx}").get(f"{ps.HR_DEVICE_STATUS}.{idx}")
        except SnmpError:
            pass
        break
    try:
        colorants = {k: v for k, r in c.walk_table([ps.COLORANT_VALUE], max_rows=16).items()
                     for v in [r.get(ps.COLORANT_VALUE, b"")]}
        sup = c.walk_table([ps.SUP_CLASS, ps.SUP_COLORANT_IDX, ps.SUP_DESCR, ps.SUP_MAX, ps.SUP_LEVEL], max_rows=24)
        out["supplies"] = ps.decode_supplies(sup, {k: ps._text(v) for k, v in colorants.items()})
    except SnmpError:
        pass
    try:
        out["inputs"] = ps.decode_inputs(c.walk_table([ps.IN_MAX, ps.IN_LEVEL, ps.IN_NAME, ps.IN_DESCR], max_rows=12))
    except SnmpError:
        pass
    try:
        out["alerts"] = ps.decode_alerts(c.walk_table([ps.ALERT_SEVERITY, ps.ALERT_CODE, ps.ALERT_DESCR], max_rows=12))
    except SnmpError:
        pass
    try:
        lines = [ps._text(r.get(ps.CONSOLE_TEXT)) for r in c.walk_table([ps.CONSOLE_TEXT], max_rows=6).values()]
        out["console"] = " | ".join(x for x in lines if x)
    except SnmpError:
        pass
    return out


class PrinterProbe:
    """Помнит IP каждого принтера и когда последний раз опрашивали SNMP."""
    SNMP_EVERY = 5.0          # секунд между SNMP-опросами одного принтера
    SNMP_RETRY_FAILED = 60.0  # если принтер не отвечает по SNMP — пробуем раз в минуту

    def __init__(self, get_overrides=lambda: {}, community="public", enabled=lambda: True):
        self.get_overrides = get_overrides
        self.community = community
        self.enabled = enabled
        self._hosts = {}
        self._snmp_cache = {}     # name -> (time, data)
        self._snmp_fail = {}      # name -> time

    def reset(self):
        """После смены настроек — заново определить адреса и опросить сразу."""
        self._hosts.clear()
        self._snmp_cache.clear()
        self._snmp_fail.clear()

    def host_for(self, name, spooler):
        ov = (self.get_overrides() or {}).get(name, "").strip()
        if ov:
            return ov
        if name in self._hosts:
            return self._hosts[name]
        port = (spooler or {}).get("port", "")
        host = host_from_port(port) or host_from_registry(port, name)
        self._hosts[name] = host
        if host:
            log.info("Принтер %s: адрес для SNMP %s (порт %s)", name, host, port)
        return host

    def probe(self, name):
        """→ (spooler | None, snmp | None, host | None)"""
        try:
            spooler = read_spooler(name)
        except Exception as e:
            log.debug("Очередь %s: %s", name, e)
            spooler = None
        snmp = None
        host = None
        if self.enabled():
            host = self.host_for(name, spooler)
            now = time.monotonic()
            if host and now - self._snmp_fail.get(name, -1e9) > self.SNMP_RETRY_FAILED:
                t, cached = self._snmp_cache.get(name, (-1e9, None))
                if now - t < self.SNMP_EVERY:
                    snmp = cached
                else:
                    try:
                        socket.gethostbyname(host)
                        snmp = read_snmp(host, self.community)
                    except OSError:
                        snmp = None
                    if snmp is None:
                        self._snmp_fail[name] = now
                    self._snmp_cache[name] = (now, snmp)
        return spooler, snmp, host
