"""Минимальный SNMP v1/v2c клиент (только чтение: GET / GETNEXT) без внешних библиотек.

Зачем: Windows (очередь печати) почти никогда не знает о замятии, чернилах и бумаге у
сетевых принтеров — для них статус в очереди обычно просто 0 («Готов»). А сам принтер
по SNMP отдаёт стандартные таблицы (RFC 3805 Printer MIB, RFC 2790 Host Resources):
уровни чернил по цветам, лотки/рулоны, ошибки (замятие, нет бумаги, открыта крышка),
текстовые предупреждения на языке принтера и состояние «печатает/простаивает».
"""
import itertools
import socket

_req_ids = itertools.count(1000)

# ───────────── BER-кодирование ─────────────
INTEGER, OCTET_STRING, NULL, OID, SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
IPADDRESS, COUNTER32, GAUGE32, TIMETICKS, COUNTER64 = 0x40, 0x41, 0x42, 0x43, 0x46
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = 0x80, 0x81, 0x82
GET, GETNEXT, RESPONSE = 0xA0, 0xA1, 0xA2


class SnmpError(Exception):
    pass


class EndOfMib:
    """Значение «дальше ничего нет» / «нет такого объекта»."""
    def __repr__(self):
        return "EndOfMib"


END = EndOfMib()


def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag, payload):
    return bytes([tag]) + _len(len(payload)) + payload


def enc_int(v):
    n = max(1, (v.bit_length() + 8) // 8)
    return tlv(INTEGER, v.to_bytes(n, "big", signed=True))


def enc_oid(oid):
    parts = [int(x) for x in str(oid).strip(".").split(".")]
    if len(parts) < 2:
        raise SnmpError(f"плохой OID {oid}")
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return tlv(OID, bytes(out))


def build_request(pdu_type, oids, community="public", version=0, req_id=None):
    req_id = next(_req_ids) if req_id is None else req_id
    vbs = b"".join(tlv(SEQUENCE, enc_oid(o) + tlv(NULL, b"")) for o in oids)
    pdu = tlv(pdu_type, enc_int(req_id) + enc_int(0) + enc_int(0) + tlv(SEQUENCE, vbs))
    return tlv(SEQUENCE, enc_int(version) + tlv(OCTET_STRING, community.encode()) + pdu), req_id


def _read_tlv(data, pos):
    tag = data[pos]
    pos += 1
    ln = data[pos]
    pos += 1
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    end = pos + ln
    if end > len(data):
        raise SnmpError("обрезанный пакет")
    return tag, data[pos:end], end


def dec_oid(b):
    if not b:
        return ""
    parts = [b[0] // 40, b[0] % 40]
    v = 0
    for x in b[1:]:
        v = (v << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(v)
            v = 0
    return ".".join(map(str, parts))


def decode_value(tag, payload):
    if tag == INTEGER:
        return int.from_bytes(payload, "big", signed=True) if payload else 0
    if tag in (COUNTER32, GAUGE32, TIMETICKS, COUNTER64):
        return int.from_bytes(payload, "big") if payload else 0
    if tag == OCTET_STRING:
        return bytes(payload)
    if tag == OID:
        return dec_oid(payload)
    if tag == IPADDRESS:
        return ".".join(str(x) for x in payload)
    if tag == NULL:
        return None
    if tag in (NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB):
        return END
    return bytes(payload)


def parse_message(data):
    """→ (version, community, pdu_type, req_id, error_status, error_index, [(oid, value)])"""
    tag, msg, _ = _read_tlv(data, 0)
    if tag != SEQUENCE:
        raise SnmpError("не SNMP")
    t, v, p = _read_tlv(msg, 0)
    version = decode_value(t, v)
    t, v, p = _read_tlv(msg, p)
    community = bytes(v).decode("latin-1")
    pdu_type, pdu, _ = _read_tlv(msg, p)
    t, v, p = _read_tlv(pdu, 0)
    req_id = decode_value(t, v)
    t, v, p = _read_tlv(pdu, p)
    err = decode_value(t, v)
    t, v, p = _read_tlv(pdu, p)
    err_idx = decode_value(t, v)
    t, vbl, p = _read_tlv(pdu, p)
    out = []
    q = 0
    while q < len(vbl):
        t, vb, q = _read_tlv(vbl, q)
        t1, o, r = _read_tlv(vb, 0)
        t2, val, _ = _read_tlv(vb, r)
        out.append((dec_oid(o), decode_value(t2, val)))
    return version, community, pdu_type, req_id, err, err_idx, out


def build_response(req_id, varbinds, community="public", version=0, error=0, error_index=0):
    """Ответ агента — нужен для тестов (поддельный принтер)."""
    def enc_val(v):
        if v is END:
            return tlv(END_OF_MIB, b"")
        if isinstance(v, bool) or isinstance(v, int):
            return enc_int(int(v))
        if isinstance(v, (bytes, bytearray)):
            return tlv(OCTET_STRING, bytes(v))
        if isinstance(v, str):
            return tlv(OCTET_STRING, v.encode("utf-8"))
        return tlv(NULL, b"")
    vbs = b"".join(tlv(SEQUENCE, enc_oid(o) + enc_val(v)) for o, v in varbinds)
    pdu = tlv(RESPONSE, enc_int(req_id) + enc_int(error) + enc_int(error_index) + tlv(SEQUENCE, vbs))
    return tlv(SEQUENCE, enc_int(version) + tlv(OCTET_STRING, community.encode()) + pdu)


# ───────────── клиент ─────────────
def oid_tuple(oid):
    return tuple(int(x) for x in str(oid).strip(".").split(".") if x)


class SnmpClient:
    def __init__(self, host, community="public", port=161, timeout=1.0, retries=1, version=0):
        self.host, self.port = host, port
        self.community, self.timeout, self.retries, self.version = community, timeout, retries, version

    def _request(self, pdu_type, oids):
        pkt, req_id = build_request(pdu_type, oids, self.community, self.version)
        last = None
        for _ in range(self.retries + 1):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.settimeout(self.timeout)
                s.sendto(pkt, (self.host, self.port))
                while True:
                    data, _addr = s.recvfrom(65535)
                    v, c, t, rid, err, idx, vbs = parse_message(data)
                    if rid != req_id:
                        continue           # чужой/опоздавший ответ
                    if err:
                        raise SnmpError(f"SNMP error-status {err} (index {idx})")
                    return vbs
            except socket.timeout as e:
                last = e
            except OSError as e:
                last = e
                break
            finally:
                s.close()
        raise SnmpError(f"нет ответа от {self.host}: {last}")

    def get(self, *oids):
        return {o: v for o, v in self._request(GET, list(oids))}

    def walk_table(self, columns, max_rows=64):
        """Обход таблицы сразу по нескольким колонкам (один запрос на строку).
        → {суффикс_индекса: {колонка: значение}}"""
        rows = {}
        current = list(columns)
        active = [True] * len(columns)
        for _ in range(max_rows):
            ask = [current[i] for i in range(len(columns)) if active[i]]
            if not ask:
                break
            try:
                vbs = self._request(GETNEXT, ask)
            except SnmpError as e:
                if "error-status 2" in str(e):   # v1: noSuchName — таблица кончилась
                    break
                raise
            j = 0
            for i, col in enumerate(columns):
                if not active[i]:
                    continue
                oid, val = vbs[j]
                j += 1
                if val is END or not oid_tuple(oid)[:len(oid_tuple(col))] == oid_tuple(col) \
                        or oid_tuple(oid) <= oid_tuple(current[i]):
                    active[i] = False
                    continue
                suffix = oid[len(col) + 1:]
                rows.setdefault(suffix, {})[col] = val
                current[i] = oid
        return rows
