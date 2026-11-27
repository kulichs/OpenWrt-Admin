"""Prehled VLAN napric routerem a AP (jen cteni) + kontrola konzistence.

Kazde zarizeni se cte jednim prikazem (`uci -X show network` a `wireless`, klice a hesla se odfiltruji uz na
zarizeni). VLAN se paruji podle toho, jak jde provoz po kabelu k routeru (uplink): cislo VLAN, nebo „bez tagu“
(nativni). Jmena siti se na zarizenich casto lisi (HOST / host), ID VLAN ne.

Podporovane zpusoby zapisu VLAN:
  - swconfig (starsi HW se switchem): `switch_vlan` s porty "0t 2t 1", rozhrani eth0.<vid>
  - DSA (novejsi HW): `bridge-vlan` na mostu (porty "lan1:t" "lan2:u*"), rozhrani br-lan.<vid>
  - 8021q na portu bez switche: eth1.<vid> (sekce device type 8021q, nebo jen jmeno v portech mostu)
  - stary zapis (19.07): interface type=bridge + ifname
"""
from __future__ import annotations

import ipaddress
import re

from .dhcp import parse_uci

# klice a hesla se odfiltruji uz na zarizeni (-e = POSIX, funguje i v BusyBox grep)
SECRET = " ".join(f"-e '{p}'" for p in ("private_key", "preshared_key", r"\.key=", r"\.key[0-9]=", "password",
                                         "auth_secret", "sae_password", "priv_key"))
READ_CMD = (
    f"echo @@net; uci -X show network 2>/dev/null | grep -v {SECRET}\n"
    f"echo @@wifi; uci -X show wireless 2>/dev/null | grep -v {SECRET}\n"
    "echo @@end\n"
)
TAG_RE = re.compile(r"^(.+)\.(\d{1,4})$")
NATIVE = "native"


def _l(v) -> list[str]:
    if v is None or v == "":
        return []
    vals = v if isinstance(v, list) else [v]
    out = []
    for x in vals:
        out += str(x).split()
    return out


def _f(v) -> str:
    return (v[0] if isinstance(v, list) and v else v) or ""


def split(text: str) -> dict[str, str]:
    out, cur = {}, None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("@@"):
            cur = s[2:]
            out[cur] = ""
        elif cur:
            out[cur] += line + "\n"
    return out


def _sw_ports(spec: str) -> list[tuple[str, bool]]:
    """swconfig "0t 2t 1" -> [("0", True), ("2", True), ("1", False)]."""
    out = []
    for p in spec.split():
        m = re.match(r"^(\d+)([tu*]*)$", p)
        if m:
            out.append((m.group(1), "t" in m.group(2)))
    return out


def _dsa_ports(vals: list[str]) -> list[tuple[str, bool]]:
    """DSA "lan1:t" "lan2:u*" "lan3" -> [("lan1", True), ("lan2", False), ("lan3", False)]."""
    out = []
    for p in vals:
        name, _, fl = p.partition(":")
        out.append((name, "t" in fl))
    return out


def _uplink(vlans: list[tuple[int, list[tuple[str, bool]]]], cpu: set[str]) -> str:
    """Port, ktery nese nejvic tagovanych VLAN (mimo CPU) = kabel k routeru / switchi."""
    cnt: dict[str, int] = {}
    for _, ports in vlans:
        for p, tagged in ports:
            if tagged and p not in cpu:
                cnt[p] = cnt.get(p, 0) + 1
    return max(cnt, key=lambda p: (cnt[p], p)) if cnt else ""


def build_device(name: str, text: str, router: bool = False) -> dict:
    sec = split(text)
    dev = {"name": name, "router": router, "error": "", "method": "", "uplink": "", "rows": {}, "issues": []}
    if "net" not in sec:
        dev["error"] = (text or "").strip()[-300:] or "?"
        return dev
    net = parse_uci(sec.get("net", ""), "network")
    wifi = parse_uci(sec.get("wifi", ""), "wireless")
    issues = dev["issues"]

    # --- mosty a rozhrani
    bridges: dict[str, list[str]] = {}
    l2: dict[str, dict] = {}          # explicitni 8021q zarizeni: jmeno -> {base, vid}
    for sid, o in net.items():
        t = o.get("_type")
        if t == "device":
            nm = _f(o.get("name")) or sid
            if _f(o.get("type")) == "bridge":
                bridges[nm] = _l(o.get("ports")) + _l(o.get("ifname"))
            elif _f(o.get("type")) in ("8021q", "8021ad") and o.get("vid"):
                l2[nm] = {"base": _f(o.get("ifname")), "vid": int(_f(o.get("vid")))}
    ifaces: dict[str, dict] = {}
    for sid, o in net.items():
        if o.get("_type") != "interface" or sid == "loopback":
            continue
        devname = _f(o.get("device"))
        if _f(o.get("type")) == "bridge":           # stary zapis 19.07
            devname = "br-" + sid
            bridges.setdefault(devname, _l(o.get("ifname")))
        elif not devname:
            devname = _f(o.get("ifname"))
        if devname.startswith("@"):                 # alias na jine rozhrani
            continue
        ips = _l(o.get("ipaddr"))
        mask = _f(o.get("netmask"))
        ip = ""
        if ips:
            try:
                ip = str(ipaddress.ip_interface(ips[0] if "/" in ips[0] or not mask else f"{ips[0]}/{mask}"))
            except ValueError:
                ip = ips[0]
        ifaces[sid] = {"name": sid, "device": devname, "proto": _f(o.get("proto")) or "none", "ip": ip,
                       "gateway": _f(o.get("gateway")), "disabled": _f(o.get("disabled")) == "1"}

    # --- SSID -> rozhrani
    radios = {sid: o for sid, o in wifi.items() if o.get("_type") == "wifi-device"}
    ssids = []
    for sid, o in wifi.items():
        if o.get("_type") != "wifi-iface" or _f(o.get("mode")) not in ("ap", ""):
            continue
        r = radios.get(_f(o.get("device")), {})
        band = _f(r.get("band")) or {"11a": "5g", "11g": "2g", "11b": "2g"}.get(_f(r.get("hwmode")), "")
        ssids.append({"id": sid, "ssid": _f(o.get("ssid")), "radio": _f(o.get("device")), "band": band,
                      "networks": _l(o.get("network")),
                      "disabled": _f(o.get("disabled")) == "1" or _f(r.get("disabled")) == "1"})

    # --- VLAN na switchi (swconfig) nebo v mostu (DSA)
    sw_vlans: dict[int, list[tuple[str, bool]]] = {}
    for sid, o in sorted(net.items(), key=lambda kv: kv[1]["_order"]):
        if o.get("_type") == "switch_vlan":
            vid = int(_f(o.get("vid")) or _f(o.get("vlan")) or 0)
            if vid:
                sw_vlans[vid] = _sw_ports(_f(o.get("ports")) if not isinstance(o.get("ports"), list) else " ".join(o["ports"]))
    dsa: dict[str, dict[int, list[tuple[str, bool]]]] = {}
    for sid, o in net.items():
        if o.get("_type") == "bridge-vlan" and o.get("vlan"):
            dsa.setdefault(_f(o.get("device")), {})[int(_f(o.get("vlan")))] = _dsa_ports(_l(o.get("ports")))

    # CPU port swconfigu = port tagovany ve vsech VLAN (obvykle 0, 5, 6 nebo 8)
    cpu_ports: set[str] = set()
    if sw_vlans:
        common = None
        for ports in sw_vlans.values():
            tg = {p for p, t in ports if t}
            common = tg if common is None else common & tg
        cpu_ports = common or set()
        if len(cpu_ports) > 1:
            cpu_ports = {sorted(cpu_ports, key=int)[0]}
    sw_up = _uplink(list(sw_vlans.items()), cpu_ports)
    # CPU ethX = zaklad eth*.N zarizeni pouzitych v mostech/rozhranich (u swconfigu)
    used_names = {p for ps in bridges.values() for p in ps} | {i["device"] for i in ifaces.values()}
    cpu_eth = ""
    if sw_vlans:
        bases = [TAG_RE.match(n).group(1) for n in used_names if TAG_RE.match(n) and not n.startswith("br-")]
        cpu_eth = max(set(bases), key=bases.count) if bases else "eth0"
        dev["method"] = "swconfig"
        dev["uplink"] = f"port {sw_up}" if sw_up else ""
    elif dsa:
        dev["method"] = "dsa"

    # --- pripojeni: (klic na kabelu, l2 jmeno) -> kam vede (most / primo rozhrani)
    attach: dict[str, str] = {}       # l2 zarizeni -> most (nebo samo sebe)
    owners: dict[str, list[str]] = {}
    for br, ports in bridges.items():
        for p in ports:
            owners.setdefault(p, []).append(br)
    for p, brs in owners.items():
        # ve dvou mostech: plati jen jeden (podle poradi startu); pro prehled ber ten jednoucelovy (nejmene portu)
        attach[p] = min(brs, key=lambda b: (len(bridges[b]), brs.index(b)))
        if len(brs) > 1:
            issues.append(("err", "vl.e_two_bridges", {"dev": p, "brs": ", ".join(brs)}))

    rows = dev["rows"]

    def row(key) -> dict:
        return rows.setdefault(key, {"key": key, "vid": None, "l2": [], "bridges": [], "ifaces": [], "ssids": [],
                                     "access": [], "note": "", "ssid_groups": []})

    def target_of(l2name: str) -> str:
        return attach.get(l2name, l2name)

    # 1) swconfig
    if sw_vlans:
        for vid, ports in sw_vlans.items():
            on_up = next((t for p, t in ports if p == sw_up), None) if sw_up else True
            key = NATIVE if on_up is False else vid
            rw = row(key)
            rw["vid"] = vid
            l2name = f"{cpu_eth}.{vid}"
            rw["l2"].append(l2name)
            acc = [p for p, t in ports if p not in cpu_ports and p != sw_up]
            rw["access"] += [f"port {p}" + (" (tag)" if t else "") for p, t in ports if p in acc]
            if sw_up and on_up is None:
                rw["note"] = "vl.n_not_uplink"
            if cpu_ports and not any(p in cpu_ports for p, _ in ports):
                issues.append(("warn", "vl.w_no_cpu", {"vid": vid}))
            br = target_of(l2name) if l2name in used_names else ""
            if br and br not in rw["bridges"]:
                rw["bridges"].append(br)
        # raw CPU eth v mostu
        for br, ports in bridges.items():
            if cpu_eth in ports and any(TAG_RE.match(p) and p.startswith(cpu_eth + ".") for p in ports):
                issues.append(("warn", "vl.w_raw_cpu", {"br": br, "eth": cpu_eth}))
        for n in sorted(used_names):
            m = TAG_RE.match(n)
            if m and m.group(1) == cpu_eth and int(m.group(2)) not in sw_vlans:
                issues.append(("err", "vl.e_no_switch_vlan", {"dev": n, "vid": int(m.group(2))}))

    # 2) DSA bridge-vlan
    for br, vl in dsa.items():
        up = _uplink(list(vl.items()), set())
        dev["uplink"] = dev["uplink"] or up
        for vid, ports in vl.items():
            on_up = next((t for p, t in ports if p == up), None) if up else True
            key = NATIVE if on_up is False else vid
            rw = row(key)
            rw["vid"] = vid
            rw["l2"].append(f"{br}.{vid}")
            rw["access"] += [p + (" (tag)" if t else "") for p, t in ports if p != up]
            if up and on_up is None:
                rw["note"] = "vl.n_not_uplink"
            tgt = f"{br}.{vid}"
            if tgt in used_names:
                b2 = target_of(tgt)
                if b2 not in rw["bridges"]:
                    rw["bridges"].append(b2)

    for n in sorted(used_names):
        m = TAG_RE.match(n)
        if m and m.group(1) in dsa and int(m.group(2)) not in dsa[m.group(1)]:
            issues.append(("err", "vl.e_no_switch_vlan", {"dev": n, "vid": int(m.group(2))}))

    # 3) 8021q na portu bez switche + nativni porty v mostech
    plain_up: set[str] = set()
    for n in sorted(used_names | set(l2)):
        m = TAG_RE.match(n)
        info = l2.get(n) or ({"base": m.group(1), "vid": int(m.group(2))} if m else None)
        if not info:
            continue
        base = info["base"]
        if base == cpu_eth and sw_vlans:
            continue
        if base in dsa:
            continue
        if n not in used_names:
            continue
        plain_up.add(base)
        rw = row(info["vid"])
        rw["vid"] = info["vid"]
        rw["l2"].append(n)
        tgt = target_of(n)
        if tgt not in rw["bridges"]:
            rw["bridges"].append(tgt)
    for br, ports in bridges.items():
        for p in ports:
            if TAG_RE.match(p) or p == cpu_eth or p in bridges:
                continue
            if br in dsa:
                continue
            if p.startswith(("wlan", "phy", "wg", "tun", "tap")):
                continue
            # holy port v mostu = netagovany (nativni) provoz na kabelu
            if True:
                rw = row(NATIVE)
                if br not in rw["bridges"]:
                    rw["bridges"].append(br)
                rw["l2"].append(p)
    if not dev["method"]:
        dev["method"] = "8021q" if plain_up else "plain"
        dev["uplink"] = ", ".join(sorted(plain_up))

    # --- rozhrani a SSID do radku
    by_target: dict[str, dict] = {}
    for rw in rows.values():
        for b in rw["bridges"]:
            by_target[b] = rw
        for x in rw["l2"]:
            by_target.setdefault(x, rw)
    for i in ifaces.values():
        rw = by_target.get(i["device"])
        if rw is None:
            continue
        rw["ifaces"].append(i)
    gw = [i["name"] for i in ifaces.values() if i["gateway"] and not i["disabled"]]
    if len(gw) > 1:
        issues.append(("warn", "vl.w_multi_gw", {"ifaces": ", ".join(gw)}))
    for s in ssids:
        placed = False
        for nw in s["networks"]:
            i = ifaces.get(nw)
            if not i:
                issues.append(("err", "vl.e_ssid_no_iface", {"ssid": s["ssid"], "net": nw}))
                continue
            rw = by_target.get(i["device"])
            if rw:
                rw["ssids"].append({**s, "net": nw})
                placed = True
        if not placed and s["networks"] and all(nw in ifaces for nw in s["networks"]):
            issues.append(("warn", "vl.w_ssid_local", {"ssid": s["ssid"], "net": ", ".join(s["networks"])}))
    # stejne SSID na dvou radiich v ruznych sitich
    seen: dict[str, set] = {}
    for s in ssids:
        if not s["disabled"]:
            seen.setdefault(s["ssid"], set()).update(s["networks"])
    for ss, nets in seen.items():
        if len(nets) > 1:
            issues.append(("err", "vl.e_ssid_split", {"ssid": ss, "nets": ", ".join(sorted(nets))}))
    # SSID seskupene podle jmena (pasma pohromade)
    for rw in rows.values():
        grp: dict[str, dict] = {}
        for x in rw["ssids"]:
            g = grp.setdefault(x["ssid"], {"ssid": x["ssid"], "on": [], "off": []})
            (g["off"] if x["disabled"] else g["on"]).append(x["band"] or x["radio"])
        rw["ssid_groups"] = list(grp.values())
    # AP s adresou v jine siti nez spravcovske (nativni / ta s branou do LAN)
    if not router:
        for key, rw in rows.items():
            if key == NATIVE:
                continue
            for i in rw["ifaces"]:
                if i["proto"] in ("static", "dhcp") and not i["disabled"]:
                    issues.append(("warn", "vl.w_ap_ip", {"iface": i["name"], "vid": key, "ip": i["ip"] or i["proto"]}))
    return dev


# ---------------------------------------------------------------- napric zarizenimi

def compare(devs: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Radky prehledu (klic VLAN -> bunky po zarizenich) a spolecne nalezy."""
    keys = set()
    for d in devs:
        keys |= set(d["rows"])
    order = sorted(keys, key=lambda k: (k != NATIVE, k if isinstance(k, int) else 0))
    router = next((d for d in devs if d["router"] and not d["error"]), None)
    out, issues = [], []
    for k in order:
        cells = [d["rows"].get(k) if not d["error"] else None for d in devs]
        names = set()
        for c in cells:
            for i in (c or {}).get("ifaces", []):
                names.add(i["name"].lower())
        label = ", ".join(sorted(names)) or "–"
        out.append({"key": k, "label": label, "cells": cells})
        if router and k not in router["rows"]:
            users = [d["name"] for d, c in zip(devs, cells) if c and not d["router"] and (c["ssids"] or c["ifaces"])]
            if users:
                issues.append(("err", "vl.e_not_on_router", {"vid": fmt_key(k), "devs": ", ".join(users)}))
        if router and k in router["rows"]:
            rnet = [ipaddress.ip_interface(i["ip"]).network for i in router["rows"][k]["ifaces"] if "/" in i["ip"]]
            for d, c in zip(devs, cells):
                if not c or d["router"]:
                    continue
                for i in c["ifaces"]:
                    if "/" in i["ip"] and rnet and not any(ipaddress.ip_interface(i["ip"]).ip in n for n in rnet):
                        issues.append(("warn", "vl.w_subnet", {"dev": d["name"], "iface": i["name"], "ip": i["ip"],
                                                                "net": ", ".join(map(str, rnet)), "vid": fmt_key(k)}))
    # stejne SSID v ruznych VLAN na ruznych AP
    where: dict[str, set] = {}
    for d in devs:
        for k, rw in d["rows"].items():
            for s in rw["ssids"]:
                if not s["disabled"]:
                    where.setdefault(s["ssid"], set()).add((k, d["name"]))
    for ss, kd in where.items():
        ks = {k for k, _ in kd}
        if len(ks) > 1:
            issues.append(("err", "vl.e_ssid_vlans", {"ssid": ss, "where": "; ".join(
                f"{n}: {fmt_key(k)}" for k, n in sorted(kd, key=lambda x: (str(x[0]), x[1])))}))
    return out, issues


def fmt_key(k) -> str:
    """Jazykove neutralni; @@N@@ nahradi sablona prelozenym „bez tagu“."""
    return "@@N@@" if k == NATIVE else f"VLAN {k}"


# ---------------------------------------------------------------- demo

DEMO = {
    # router s DSA: br-lan, lan4 = kabel ke switchi (VLAN 10 a 30 tagovane), dmz na samostatnem portu eth1
    "router": """@@net
network.loopback=interface
network.loopback.device='lo'
network.loopback.proto='static'
network.cfg030f15=device
network.cfg030f15.name='br-lan'
network.cfg030f15.type='bridge'
network.cfg030f15.ports='lan1' 'lan2' 'lan3' 'lan4'
network.cfg040f15=bridge-vlan
network.cfg040f15.device='br-lan'
network.cfg040f15.vlan='1'
network.cfg040f15.ports='lan1:u*' 'lan2:u*' 'lan3:u*' 'lan4:u*'
network.cfg050f15=bridge-vlan
network.cfg050f15.device='br-lan'
network.cfg050f15.vlan='10'
network.cfg050f15.ports='lan4:t'
network.cfg060f15=bridge-vlan
network.cfg060f15.device='br-lan'
network.cfg060f15.vlan='30'
network.cfg060f15.ports='lan3' 'lan4:t'
network.lan=interface
network.lan.device='br-lan.1'
network.lan.proto='static'
network.lan.ipaddr='192.168.1.1'
network.lan.netmask='255.255.255.0'
network.wan=interface
network.wan.device='wan'
network.wan.proto='dhcp'
network.guest=interface
network.guest.device='br-lan.30'
network.guest.proto='static'
network.guest.ipaddr='192.168.30.1'
network.guest.netmask='255.255.255.0'
network.iot=interface
network.iot.device='br-lan.10'
network.iot.proto='static'
network.iot.ipaddr='192.168.10.1'
network.iot.netmask='255.255.255.0'
network.cfg070f15=device
network.cfg070f15.type='8021q'
network.cfg070f15.ifname='eth1'
network.cfg070f15.vid='100'
network.cfg070f15.name='eth1.100'
network.dmz=interface
network.dmz.device='eth1.100'
network.dmz.proto='static'
network.dmz.ipaddr='192.168.100.1'
network.dmz.netmask='255.255.255.0'
network.wg0=interface
network.wg0.proto='wireguard'
@@wifi
@@end
""",
    # UniFi AP: jeden port, VLAN primo eth0.N, site bez adresy (spravne)
    "AP-Living": """@@net
network.loopback=interface
network.loopback.device='lo'
network.cfg010f15=device
network.cfg010f15.name='br-lan'
network.cfg010f15.type='bridge'
network.cfg010f15.ports='eth0'
network.lan=interface
network.lan.device='br-lan'
network.lan.proto='static'
network.lan.ipaddr='192.168.1.2'
network.lan.netmask='255.255.255.0'
network.lan.gateway='192.168.1.1'
network.br_guest=device
network.br_guest.type='bridge'
network.br_guest.name='br-guest'
network.br_guest.ports='eth0.30'
network.guest=interface
network.guest.proto='none'
network.guest.device='br-guest'
network.br_iot=device
network.br_iot.type='bridge'
network.br_iot.name='br-iot'
network.br_iot.ports='eth0.10'
network.iot=interface
network.iot.proto='none'
network.iot.device='br-iot'
@@wifi
network.x=y
wireless.radio0=wifi-device
wireless.radio0.band='2g'
wireless.radio1=wifi-device
wireless.radio1.band='5g'
wireless.wl0=wifi-iface
wireless.wl0.device='radio0'
wireless.wl0.mode='ap'
wireless.wl0.ssid='Home'
wireless.wl0.network='lan'
wireless.wl1=wifi-iface
wireless.wl1.device='radio1'
wireless.wl1.mode='ap'
wireless.wl1.ssid='Home'
wireless.wl1.network='lan'
wireless.wl2=wifi-iface
wireless.wl2.device='radio0'
wireless.wl2.mode='ap'
wireless.wl2.ssid='Guests'
wireless.wl2.network='guest'
wireless.wl3=wifi-iface
wireless.wl3.device='radio0'
wireless.wl3.mode='ap'
wireless.wl3.ssid='Home-IoT'
wireless.wl3.network='iot'
@@end
""",
    # Xiaomi se switchem (swconfig): uplink port 1, chyby pro ukazku – VLAN 10 ve dvou mostech, adresa v siti iot
    "AP-Office": """@@net
network.loopback=interface
network.loopback.device='lo'
network.cfg020f15=switch
network.cfg020f15.name='switch0'
network.cfg020f15.enable_vlan='1'
network.cfg030f15=switch_vlan
network.cfg030f15.device='switch0'
network.cfg030f15.vlan='1'
network.cfg030f15.vid='1'
network.cfg030f15.ports='6t 1 2 3'
network.cfg040f15=switch_vlan
network.cfg040f15.device='switch0'
network.cfg040f15.vlan='2'
network.cfg040f15.vid='10'
network.cfg040f15.ports='6t 1t'
network.cfg050f15=switch_vlan
network.cfg050f15.device='switch0'
network.cfg050f15.vlan='3'
network.cfg050f15.vid='30'
network.cfg050f15.ports='6t 1t'
network.cfg060f15=device
network.cfg060f15.name='br-lan'
network.cfg060f15.type='bridge'
network.cfg060f15.ports='eth0.1' 'eth0.10'
network.lan=interface
network.lan.device='br-lan'
network.lan.proto='static'
network.lan.ipaddr='192.168.1.3'
network.lan.netmask='255.255.255.0'
network.lan.gateway='192.168.1.1'
network.cfg070f15=device
network.cfg070f15.name='br-iot'
network.cfg070f15.type='bridge'
network.cfg070f15.ports='eth0.10'
network.IOT=interface
network.IOT.device='br-iot'
network.IOT.proto='static'
network.IOT.ipaddr='192.168.10.3'
network.IOT.netmask='255.255.255.0'
network.IOT.gateway='192.168.10.1'
network.cfg080f15=device
network.cfg080f15.name='br-guest'
network.cfg080f15.type='bridge'
network.cfg080f15.ports='eth0.30'
network.GUEST=interface
network.GUEST.device='br-guest'
network.GUEST.proto='none'
@@wifi
wireless.radio0=wifi-device
wireless.radio0.band='2g'
wireless.radio1=wifi-device
wireless.radio1.band='5g'
wireless.d0=wifi-iface
wireless.d0.device='radio0'
wireless.d0.mode='ap'
wireless.d0.ssid='Home'
wireless.d0.network='lan'
wireless.d1=wifi-iface
wireless.d1.device='radio1'
wireless.d1.mode='ap'
wireless.d1.ssid='Home'
wireless.d1.network='lan'
wireless.d2=wifi-iface
wireless.d2.device='radio0'
wireless.d2.mode='ap'
wireless.d2.ssid='Home-IoT'
wireless.d2.network='IOT'
wireless.d3=wifi-iface
wireless.d3.device='radio1'
wireless.d3.mode='ap'
wireless.d3.ssid='Guests'
wireless.d3.network='GUEST'
@@end
""",
}
