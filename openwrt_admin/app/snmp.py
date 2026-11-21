"""Minimalni SNMPv2c klient (jen cteni: GETBULK / walk) nad asyncio UDP, bez zavislosti.

Staci na standardni MIB spravovanych switchu (Netgear, TP-Link, Cisco SG, Zyxel…):
- SNMPv2-MIB system, IF-MIB (stav, rychlost, nazvy portu), BRIDGE-MIB / Q-BRIDGE-MIB (MAC adresy na portech, VLANy).
"""
from __future__ import annotations

import asyncio
import random

# ---------------------------------------------------------------- BER


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(v: int) -> bytes:
    b = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, b)


def _oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.strip(".").split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _read(buf: bytes, i: int) -> tuple[int, bytes, int]:
    """-> (tag, obsah, index za prvkem)"""
    tag = buf[i]
    n = buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def _dec_oid(b: bytes) -> str:
    parts = [b[0] // 40, b[0] % 40]
    v = 0
    for x in b[1:]:
        v = (v << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(v)
            v = 0
    return ".".join(map(str, parts))


END = object()  # endOfMibView / noSuchObject / noSuchInstance


def _value(tag: int, b: bytes):
    if tag in (0x02,):
        return int.from_bytes(b, "big", signed=True) if b else 0
    if tag in (0x41, 0x42, 0x43, 0x46):  # Counter32, Gauge32, TimeTicks, Counter64
        return int.from_bytes(b, "big") if b else 0
    if tag == 0x04:
        return bytes(b)
    if tag == 0x06:
        return _dec_oid(b)
    if tag == 0x40:
        return ".".join(str(x) for x in b)
    if tag in (0x80, 0x81, 0x82):
        return END
    return None


def encode_getbulk(community: str, req_id: int, oid: str, max_rep: int = 25) -> bytes:
    vb = _tlv(0x30, _tlv(0x30, _oid(oid) + b"\x05\x00"))
    pdu = _tlv(0xA5, _int(req_id) + _int(0) + _int(max_rep) + vb)
    return _tlv(0x30, _int(1) + _tlv(0x04, community.encode()) + pdu)


def decode_response(buf: bytes) -> tuple[int, int, list[tuple[str, object]]]:
    """-> (request id, error status, [(oid, hodnota)])"""
    _, msg, _ = _read(buf, 0)
    i = 0
    _, _, i = _read(msg, i)          # verze
    _, _, i = _read(msg, i)          # community
    tag, pdu, _ = _read(msg, i)
    if tag != 0xA2:
        raise ValueError(f"unexpected PDU 0x{tag:02x}")
    j = 0
    _, rid, j = _read(pdu, j)
    _, err, j = _read(pdu, j)
    _, _, j = _read(pdu, j)          # error index
    _, vbl, _ = _read(pdu, j)
    out = []
    k = 0
    while k < len(vbl):
        _, vb, k = _read(vbl, k)
        _, ob, m = _read(vb, 0)
        vt, vv, _ = _read(vb, m)
        out.append((_dec_oid(ob), _value(vt, vv)))
    return int.from_bytes(rid, "big", signed=True), int.from_bytes(err, "big"), out


# ---------------------------------------------------------------- UDP


class _Proto(asyncio.DatagramProtocol):
    def __init__(self):
        self.waiters: dict[int, asyncio.Future] = {}

    def datagram_received(self, data, addr):
        try:
            rid, err, vbs = decode_response(data)
        except (ValueError, IndexError):
            return
        f = self.waiters.pop(rid, None)
        if f and not f.done():
            f.set_result((err, vbs))

    def error_received(self, exc):
        for f in self.waiters.values():
            if not f.done():
                f.set_exception(exc)
        self.waiters.clear()


class SnmpError(Exception):
    pass


class Client:
    """async with Client(host, community) as c: rows = await c.walk("1.3.6.1.2.1.2.2.1.8")"""

    def __init__(self, host: str, community: str = "public", port: int = 161, timeout: float = 2.0, retries: int = 2):
        self.host, self.port, self.community = host, port, community
        self.timeout, self.retries = timeout, retries
        self.transport = None
        self.proto: _Proto | None = None

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        self.transport, self.proto = await loop.create_datagram_endpoint(_Proto, remote_addr=(self.host, self.port))
        return self

    async def __aexit__(self, *exc):
        if self.transport:
            self.transport.close()

    async def getbulk(self, oid: str, max_rep: int = 25) -> list[tuple[str, object]]:
        loop = asyncio.get_running_loop()
        for _ in range(self.retries + 1):
            rid = random.randint(1, 2**31 - 1)
            fut = loop.create_future()
            self.proto.waiters[rid] = fut
            self.transport.sendto(encode_getbulk(self.community, rid, oid, max_rep))
            try:
                err, vbs = await asyncio.wait_for(fut, self.timeout)
            except asyncio.TimeoutError:
                self.proto.waiters.pop(rid, None)
                continue
            if err:
                raise SnmpError(f"SNMP error status {err}")
            return vbs
        raise SnmpError(f"SNMP timeout ({self.host})")

    async def walk(self, base: str, limit: int = 5000) -> dict[str, object]:
        """Vsechny hodnoty pod `base` -> {pripona OID za base: hodnota}."""
        base = base.strip(".")
        prefix = base + "."
        out: dict[str, object] = {}
        cur = base
        while len(out) < limit:
            vbs = await self.getbulk(cur)
            if not vbs:
                break
            done = False
            for oid, val in vbs:
                if val is END or not oid.startswith(prefix):
                    done = True
                    break
                out[oid[len(prefix):]] = val
                cur = oid
            if done:
                break
        return out
