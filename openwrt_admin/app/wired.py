"""Kabelova zarizeni na portech AP (AP slouzi zaroven jako switch) a retezeni AP.

Zdroj: tabulka MAC adres switche (`swconfig dev switch0 get arl_table`). Na kazdem AP:
- uplink = port, na kterem je MAC routeru (brany; z ARP tabulky AP),
- CPU port = port tagovany ve vsech switch_vlan (krome uplinku), zalozne port s vlastni MAC AP
  nebo s nejvice Wi-Fi klienty tohoto AP,
- ostatni porty = lokalni zarizeni.
Kdyz stejnou MAC vidi lokalne vic AP (AP zapojene za sebou), patri tomu nejniz v retezu.
Neumi-li switch tabulku vypsat (jiny cip, DSA), AP se preskoci a zarizeni zustanou u routeru.
"""
from __future__ import annotations

import re

from .collector import APState

PORT_RE = re.compile(r"^(\d+)(t?)")


def _tagged_everywhere(vlans: list[str]) -> set[str]:
    sets = []
    for ports in vlans:
        tagged = set()
        for p in ports.split():
            m = PORT_RE.match(p)
            if m and m.group(2) == "t":
                tagged.add(m.group(1))
        sets.append(tagged)
    return set.intersection(*sets) if sets else set()


def _gateway_ip(ap: APState, router: APState | None) -> str:
    if router:
        return router.host.split(":")[0]
    lan = next((i for i in ap.interfaces if i["name"] == "lan"), None)
    return (lan or {}).get("gateway", "")


def local_ports(ap: APState, router: APState | None) -> dict[str, str]:
    """{mac: "switch0:5"} – zarizeni na lokalnich portech AP (bez uplinku a CPU portu)."""
    if not ap.arl:
        return {}
    gw_ip = _gateway_ip(ap, router)
    gw_macs = {mac for mac, ip in ap.arp.items() if ip == gw_ip}
    uplink = {port for port, macs in ap.arl.items() if gw_macs & set(macs)}
    if not uplink:
        return {}  # bez uplinku nejde poznat, co je lokalni
    up_nums = {p.split(":")[1] for p in uplink}
    cpu_nums = _tagged_everywhere(ap.switch_vlans) - up_nums
    cpu = {p for p in ap.arl if p.split(":")[1] in cpu_nums}
    if not cpu:
        own = {p for p, macs in ap.arl.items() if ap.mac and ap.mac in macs}
        if own:
            cpu = own
        else:
            wifi = {c["mac"] for c in ap.clients}
            best = max(ap.arl.items(), key=lambda kv: len(wifi & set(kv[1])), default=(None, []))
            if best[0] and wifi & set(best[1]):
                cpu = {best[0]}
    out = {}
    for port, macs in ap.arl.items():
        if port in uplink or port in cpu:
            continue
        for mac in macs:
            out[mac] = port
    return out


def locate(aps: list[APState], router: APState | None, leases: dict[str, dict]) -> dict:
    """-> {"by_ap": {ap: [zarizeni]}, "parent": {ap: nadrazeny_ap}, "placed": {mac}, "ap_macs": {mac}}"""
    local = {ap.name: local_ports(ap, router) for ap in aps if ap.online}
    ap_mac = {ap.mac: ap.name for ap in aps if ap.mac}
    wifi = {c["mac"] for ap in aps for c in ap.clients}
    gw = set()
    for ap in aps:
        gw_ip = _gateway_ip(ap, router)
        gw |= {mac for mac, ip in ap.arp.items() if ip == gw_ip}
    arp_ip = {}
    for ap in aps:
        for mac, ip in ap.arp.items():
            arp_ip.setdefault(mac, ip)

    # hloubka AP = kolik jinych AP ho vidi lokalne (cim hloubeji v retezu, tim vic)
    depth = {ap.name: sum(1 for o, loc in local.items() if o != ap.name and ap.mac and ap.mac in loc) for ap in aps}
    parent = {}
    for ap in aps:
        seen_by = [o for o, loc in local.items() if o != ap.name and ap.mac and ap.mac in loc]
        if seen_by:
            parent[ap.name] = max(seen_by, key=lambda o: depth.get(o, 0))

    by_ap: dict[str, list[dict]] = {}
    placed = set()
    candidates: dict[str, list[str]] = {}
    for name, loc in local.items():
        for mac in loc:
            if mac in wifi or mac in ap_mac or mac in gw:
                continue
            candidates.setdefault(mac, []).append(name)
    for mac, names in candidates.items():
        owner = max(names, key=lambda n: depth.get(n, 0))
        l = leases.get(mac, {})
        by_ap.setdefault(owner, []).append({
            "mac": mac, "ip": l.get("ip") or arp_ip.get(mac, ""), "hostname": l.get("hostname", ""),
            "port": local[owner][mac].split(":")[1],
        })
        placed.add(mac)
    for v in by_ap.values():
        v.sort(key=lambda d: (int(d["port"]), (d["hostname"] or "~").lower(), d["mac"]))
    return {"by_ap": by_ap, "parent": parent, "placed": placed, "ap_macs": set(ap_mac)}
