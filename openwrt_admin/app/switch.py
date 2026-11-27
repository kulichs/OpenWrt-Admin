"""Spravovane switche pres SNMP (jen cteni): porty, rychlosti, MAC adresy na portech, VLANy.

Vysledek je APState s kind="switch" – tabulka MAC adres (`arl`) ma stejny tvar jako u AP se swconfig,
takze umisteni zarizeni, retezeni (AP za switchem), mapa, panel Porty i diagnostika funguji stejne.

Standardni MIB (Netgear GS724T, TP-Link, Cisco SG, Zyxel, HP…):
- system: sysDescr, sysUpTime, sysName
- IF-MIB: ifType, ifOperStatus, ifHighSpeed (ifSpeed), ifName, ifAlias
- BRIDGE-MIB: dot1dBaseBridgeAddress (MAC switche), dot1dBasePortIfIndex (port -> ifIndex), dot1dTpFdbPort
- Q-BRIDGE-MIB: dot1qTpFdbPort (MAC po VLAN -> port), dot1qVlanStaticEgressPorts / UntaggedPorts (VLANy na portech)
- EtherLike-MIB: dot3StatsDuplexStatus (volitelne)
"""
from __future__ import annotations

import logging
import time

from .collector import APState
from .snmp import Client, SnmpError

log = logging.getLogger("openwrt_admin.switch")

SYSTEM = "1.3.6.1.2.1.1"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HIGHSPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"
BRIDGE_ADDR = "1.3.6.1.2.1.17.1.1"
BASE_PORT_IF = "1.3.6.1.2.1.17.1.4.1.2"
TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
Q_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
Q_VLAN_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
Q_VLAN_EGRESS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
Q_VLAN_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.3.1.4"
Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"          # dot1qPvid (index = bridge port)
DUPLEX = "1.3.6.1.2.1.10.7.2.1.19"

ETHER_TYPES = {6, 62, 69, 117}  # ethernetCsmacd, fastEther, fastEtherFX, gigabitEthernet


def _mac(b) -> str:
    if isinstance(b, (bytes, bytearray)) and len(b) == 6:
        return ":".join(f"{x:02x}" for x in b)
    return ""


def _mac_from_index(parts: list[str]) -> str:
    try:
        return ":".join(f"{int(x):02x}" for x in parts)
    except ValueError:
        return ""


def _text(v) -> str:
    if isinstance(v, (bytes, bytearray)):
        return v.decode(errors="replace").strip("\x00 ").strip()
    return "" if v is None else str(v)


def ports_of_bitmap(b) -> set[int]:
    """PortList (Q-BRIDGE-MIB): bit 1 = nejvyssi bit prvniho bajtu = port 1."""
    out = set()
    if isinstance(b, (bytes, bytearray)):
        for i, byte in enumerate(b):
            for bit in range(8):
                if byte & (0x80 >> bit):
                    out.add(i * 8 + bit + 1)
    return out


def parse_fdb(q_fdb: dict, tp_fdb: dict) -> dict[int, dict[str, set[int]]]:
    """-> {bridge port: {mac: {vlan, ...}}} z Q-BRIDGE (vlan.m1…m6) nebo BRIDGE-MIB (m1…m6, bez VLAN)."""
    out: dict[int, dict[str, set[int]]] = {}
    for idx, port in q_fdb.items():
        p = idx.split(".")
        if len(p) != 7 or not isinstance(port, int) or port <= 0:
            continue
        mac = _mac_from_index(p[1:])
        if mac:
            out.setdefault(port, {}).setdefault(mac, set()).add(int(p[0]))
    if not out:
        for idx, port in tp_fdb.items():
            p = idx.split(".")
            if len(p) != 6 or not isinstance(port, int) or port <= 0:
                continue
            mac = _mac_from_index(p)
            if mac:
                out.setdefault(port, {}).setdefault(mac, set())
    return out


def build_state(name: str, host: str, d: dict) -> APState:
    """Data z SNMP walku (slovnik OID tabulek) -> APState(kind="switch")."""
    st = APState(name=name, host=host, kind="switch", online=True, updated=time.time())
    sysd = d.get("system") or {}
    descr = _text(sysd.get("1.0"))
    st.hostname = _text(sysd.get("5.0"))
    st.uptime = int(sysd.get("3.0") or 0) // 100
    parts = [x.strip() for x in descr.split(",")]
    st.model = parts[0] if parts else descr
    st.release = ", ".join(parts[1:]) if len(parts) > 1 else ""
    st.mac = _mac((d.get("bridge_addr") or {}).get("0"))

    if_type = {int(k): v for k, v in (d.get("if_type") or {}).items() if k.isdigit()}
    oper = {int(k): v for k, v in (d.get("if_oper") or {}).items() if k.isdigit()}
    hs = {int(k): v for k, v in (d.get("if_highspeed") or {}).items() if k.isdigit()}
    sp = {int(k): v for k, v in (d.get("if_speed") or {}).items() if k.isdigit()}
    names = {int(k): _text(v) for k, v in (d.get("if_name") or {}).items() if k.isdigit()}
    alias = {int(k): _text(v) for k, v in (d.get("if_alias") or {}).items() if k.isdigit()}
    duplex = {int(k): v for k, v in (d.get("duplex") or {}).items() if k.isdigit()}
    port_if = {int(k): v for k, v in (d.get("base_port_if") or {}).items() if k.isdigit() and isinstance(v, int)}
    if not port_if:  # bez BRIDGE-MIB: port = ifIndex
        port_if = {i: i for i in if_type}

    # VLANy na portech
    egress = {int(k): ports_of_bitmap(v) for k, v in (d.get("vlan_egress") or {}).items() if k.isdigit()}
    untagged = {int(k): ports_of_bitmap(v) for k, v in (d.get("vlan_untagged") or {}).items() if k.isdigit()}
    pvid = {int(k): v for k, v in (d.get("pvid") or {}).items() if k.isdigit() and isinstance(v, int)}

    for port in sorted(port_if):
        ifi = port_if[port]
        if if_type and if_type.get(ifi) not in ETHER_TYPES:
            continue  # CPU, LAG, VLAN rozhrani…
        up = oper.get(ifi) == 1
        speed = hs.get(ifi) or (sp.get(ifi, 0) // 1_000_000 if sp.get(ifi) else 0)
        st.ports.append({
            "id": f"sw:{port}", "num": str(port), "up": up, "speed": int(speed) if up else 0,
            "duplex": {2: "half", 3: "full"}.get(duplex.get(ifi), "") if up else "",
            "kind": "switch", "name": names.get(ifi, ""), "alias": alias.get(ifi, ""), "ifindex": ifi,
            "vlans_u": sorted(v for v, ps in untagged.items() if port in ps),
            "vlans_t": sorted(v for v, ps in egress.items() if port in ps and port not in untagged.get(v, set())),
            "pvid": pvid.get(port, 0),
        })

    fdb = parse_fdb(d.get("q_fdb") or {}, d.get("tp_fdb") or {})
    st.arl = {f"sw:{p}": sorted(macs) for p, macs in fdb.items()}
    return st


TABLES = {
    "system": SYSTEM, "if_type": IF_TYPE, "if_oper": IF_OPER, "if_highspeed": IF_HIGHSPEED, "if_speed": IF_SPEED,
    "if_name": IF_NAME, "if_alias": IF_ALIAS, "bridge_addr": BRIDGE_ADDR, "base_port_if": BASE_PORT_IF,
    "q_fdb": Q_FDB_PORT, "vlan_egress": Q_VLAN_EGRESS, "vlan_untagged": Q_VLAN_UNTAGGED, "duplex": DUPLEX,
    "pvid": Q_PVID,
}


async def collect_switch(name: str, host: str, community: str, port: int = 161, timeout: float = 2.0) -> APState:
    d: dict[str, dict] = {}
    try:
        async with Client(host, community, port=port, timeout=timeout) as c:
            d["system"] = await c.walk(SYSTEM)
            if not d["system"]:
                raise SnmpError("no SNMP data (community?)")
            for key, oid in TABLES.items():
                if key == "system":
                    continue
                try:
                    d[key] = await c.walk(oid)
                except SnmpError:
                    d[key] = {}
            if not d.get("q_fdb"):
                d["tp_fdb"] = await c.walk(TP_FDB_PORT)
        return build_state(name, host, d)
    except (OSError, SnmpError) as e:
        st = APState(name=name, host=host, kind="switch", updated=time.time())
        st.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("%s (%s): %s", name, host, st.error)
        return st


HC_IN = "1.3.6.1.2.1.31.1.1.1.6"    # ifHCInOctets (64 bit)
HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"  # ifHCOutOctets
IN32 = "1.3.6.1.2.1.2.2.1.10"       # ifInOctets (32 bit, zalozne)
OUT32 = "1.3.6.1.2.1.2.2.1.16"      # ifOutOctets


def parse_counters(cin: dict, cout: dict, bits: int) -> dict[int, tuple[int, int, int]]:
    """Walk citacu -> {ifIndex: (in, out, bitu citace)}."""
    out = {}
    for k, v in cin.items():
        w = cout.get(k)
        if k.isdigit() and isinstance(v, int) and isinstance(w, int):
            out[int(k)] = (v, w, bits)
    return out


async def read_counters(host: str, community: str, port: int = 161, timeout: float = 2.0) -> dict[int, tuple[int, int, int]]:
    """Citace bajtu vsech rozhrani switche (pro rychlost portu). Chyby vyhazuje dal (OSError, SnmpError)."""
    async with Client(host, community, port=port, timeout=timeout) as c:
        cin = await c.walk(HC_IN)
        if cin:
            return parse_counters(cin, await c.walk(HC_OUT), 64)
        return parse_counters(await c.walk(IN32), await c.walk(OUT32), 32)
