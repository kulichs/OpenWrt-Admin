"""Firewall routeru (fw4): zony, pruchody, pravidla, presmerovani portu a co je otevrene z internetu.

Jen cteni (0.21.0). Cte se uci konfigurace, poradi zon na rozhranich z `fw4 print` (ktera zona dostane provoz
z daneho zarizeni jako prvni) a naslouchajici sluzby z netstat – z toho se pocita, co je dostupne z internetu.
"""
from __future__ import annotations

import ipaddress
import re
import time

from .dhcp import _first, parse_uci

READ_CMD = r"""echo @@fwver; command -v fw4 >/dev/null 2>&1 && echo fw4 || echo fw3
echo @@uci; uci -X -q show firewall
echo @@dispatch; fw4 print 2>/dev/null | grep -E 'iifname .* jump (input|forward)_'
echo @@listen; netstat -tulpn 2>/dev/null || ss -tulpn 2>/dev/null
echo @@route; d=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}'); echo "dev $d"
echo @@wanip; [ -n "$d" ] && ip -4 -o addr show dev "$d" 2>/dev/null | awk '{print $4}' | cut -d/ -f1
echo @@nftd; ls /etc/nftables.d/ 2>/dev/null
echo @@addr; ip -4 -o addr show 2>/dev/null | awk '{print $2, $4}'
echo @@me; echo "$SSH_CLIENT"
echo @@cron; grep -F '# owadmin ' /etc/crontabs/root 2>/dev/null
echo @@now; date '+%Y-%m-%d %H:%M'
echo @@end
"""

WAN_NETS = ("wan", "wan6")


def _list(v) -> list[str]:
    if v is None or v == "":
        return []
    vals = v if isinstance(v, list) else [v]
    out = []
    for x in vals:
        out += str(x).split()
    return out


def parse_dispatch(text: str) -> dict[str, dict[str, list[str]]]:
    """fw4 print -> {"input": {zona: [zarizeni]}, "forward": {...}, "order": {chain: [zony v poradi]}}."""
    out: dict = {"input": {}, "forward": {}, "order": {"input": [], "forward": []}}
    for line in (text or "").splitlines():
        m = re.search(r'iifname\s+(\{[^}]*\}|"[^"]+"|\S+)\s+jump\s+(input|forward)_(\S+)', line)
        if not m:
            continue
        devs = re.findall(r'"([^"]+)"', m.group(1)) or [m.group(1).strip('"')]
        chain, zone = m.group(2), m.group(3)
        out[chain][zone] = devs
        out["order"][chain].append(zone)
    return out


def parse_listen(text: str) -> list[dict]:
    """netstat -tulpn (BusyBox) nebo ss -tulpn."""
    out = []
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] in ("tcp", "udp") and p[1] in ("LISTEN", "UNCONN"):
            host, _, port = p[4].rpartition(":")
            m = re.search(r'users:\(\("([^"]+)"', line)
            if port.isdigit():
                out.append({"proto": p[0], "addr": host.strip("[]").split("%")[0], "port": int(port),
                            "prog": m.group(1) if m else ""})
            continue
        if len(p) < 4 or p[0] not in ("tcp", "udp", "tcp6", "udp6"):
            continue
        addr = p[3]
        host, _, port = addr.rpartition(":")
        if not port.isdigit():
            continue
        prog = p[-1] if "/" in p[-1] else ""
        out.append({"proto": p[0].rstrip("6"), "addr": host, "port": int(port),
                    "prog": prog.split("/", 1)[1] if "/" in prog else ""})
    return out


def ports_of(spec) -> list[tuple[int, int]]:
    """'22', '33434:33689', '444-65535', '443 993' -> [(od, do)]."""
    out = []
    for part in _list(spec):
        m = re.fullmatch(r"(\d+)(?:[:-](\d+))?", part)
        if m:
            a = int(m.group(1))
            out.append((a, int(m.group(2) or a)))
    return out


def port_in(port: int, spec) -> bool:
    return any(a <= port <= b for a, b in ports_of(spec))


def fmt_ports(spec) -> str:
    return ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in ports_of(spec))


def protos_of(v) -> list[str]:
    p = [x.lower() for x in _list(v)]
    if not p or "all" in p or "any" in p:
        return ["tcp", "udp"] if not p or "all" not in p else ["all"]
    return p


def build(text: str) -> dict:
    sec: dict[str, list[str]] = {}
    cur = None
    for line in (text or "").splitlines():
        if re.fullmatch(r"@@[a-z]+", line.strip()):
            cur = line.strip()[2:]
            sec.setdefault(cur, [])
        elif cur:
            sec[cur].append(line)
    uci = parse_uci("\n".join(sec.get("uci", [])), "firewall")
    order = sorted(uci.items(), key=lambda kv: kv[1]["_order"])
    disp = parse_dispatch("\n".join(sec.get("dispatch", [])))
    listen = parse_listen("\n".join(sec.get("listen", [])))
    wanips = [x.strip() for x in sec.get("wanip", []) if x.strip()]
    route_dev = next((x.split()[1] for x in sec.get("route", []) if x.startswith("dev ") and len(x.split()) > 1), "")
    fwver = (sec.get("fwver") or ["fw4"])[0].strip() or "fw4"

    zones = []
    for sid, o in order:
        if o["_type"] != "zone":
            continue
        name = _first(o.get("name")) or sid
        zones.append({"id": sid, "name": name, "networks": _list(o.get("network")),
                      "input": (_first(o.get("input")) or "ACCEPT").upper(),
                      "output": (_first(o.get("output")) or "ACCEPT").upper(),
                      "forward": (_first(o.get("forward")) or "ACCEPT").upper(),
                      "masq": str(o.get("masq", "")) == "1", "devices": disp["input"].get(name, []),
                      "enabled": str(o.get("enabled", "1")) != "0"})
    zmap = {z["name"]: z for z in zones}
    forwards = [{"id": sid, "src": _first(o.get("src")), "dest": _first(o.get("dest")),
                 "enabled": str(o.get("enabled", "1")) != "0"} for sid, o in order if o["_type"] == "forwarding"]
    rules, redirects = [], []
    editable = {"_type", "_order", "name", "src", "dest", "proto", "dest_port", "src_ip", "dest_ip", "src_mac", "family",
                "target", "enabled"}
    for pos, (sid, o) in enumerate(order):
        if o["_type"] == "rule":
            r = {"id": sid, "name": _first(o.get("name")) or sid, "src": _first(o.get("src")), "dest": _first(o.get("dest")),
                 "proto": _list(o.get("proto")), "dest_port": " ".join(_list(o.get("dest_port"))),
                 "src_ip": _list(o.get("src_ip")), "dest_ip": _list(o.get("dest_ip")), "src_mac": _list(o.get("src_mac")),
                 "icmp_type": _list(o.get("icmp_type")), "family": _first(o.get("family")),
                 "target": (_first(o.get("target")) or "ACCEPT").upper(), "enabled": str(o.get("enabled", "1")) != "0",
                 "time": {k: _first(o.get(k)) for k in ("start_time", "stop_time", "weekdays", "start_date", "stop_date",
                                                          "monthdays", "utc_time") if o.get(k)}}
            r["kind"] = "block" if r["target"] in ("DROP", "REJECT") and (r["src_mac"] or r["src_ip"]) and r["dest"] else ""
            r["pos"] = pos                                   # poradi sekce v /etc/config/firewall (pro uci reorder)
            r["extra"] = sorted(k for k in o if k not in editable)   # volby, ktere formular aplikace neumi
            rules.append(r)
        elif o["_type"] == "redirect":
            redirects.append({"id": sid, "name": _first(o.get("name")) or sid, "src": _first(o.get("src")) or "wan",
                              "dest": _first(o.get("dest")), "proto": _list(o.get("proto")) or ["tcp", "udp"],
                              "src_dport": " ".join(_list(o.get("src_dport"))), "dest_ip": _first(o.get("dest_ip")),
                              "dest_port": " ".join(_list(o.get("dest_port"))) or " ".join(_list(o.get("src_dport"))),
                              "target": (_first(o.get("target")) or "DNAT").upper(),
                              "src_ip": " ".join(_list(o.get("src_ip"))),
                              "own": sid.startswith("owa_fwd_"),
                              "enabled": str(o.get("enabled", "1")) != "0"})
    includes = [{"id": sid, "path": _first(o.get("path")), "fw4": str(o.get("fw4_compatible", "")) == "1"}
                for sid, o in order if o["_type"] == "include"]
    nftd = [x.strip() for x in sec.get("nftd", []) if x.strip() and x.strip() != "README"]

    # ---- sit ve vice zonach, zarizeni ve vice zonach
    warns: list[dict] = []
    net_zones: dict[str, list[str]] = {}
    for z in zones:
        for n in z["networks"]:
            net_zones.setdefault(n, []).append(z["name"])
    dev_zones: dict[str, list[str]] = {}
    for zname in disp["order"]["input"]:
        for d in disp["input"].get(zname, []):
            dev_zones.setdefault(d, []).append(zname)
    for n, zs in net_zones.items():
        if len(zs) > 1:
            warns.append({"level": "warn", "key": "fw.w_net_multi", "args": {"net": n, "zones": ", ".join(zs), "first": zs[0]}})
    for d, zs in dev_zones.items():
        if len(zs) > 1:
            warns.append({"level": "warn", "key": "fw.w_dev_multi", "args": {"dev": d, "zones": ", ".join(zs), "first": zs[0]}})

    # ---- WAN: ktera zona dostane provoz z internetu
    # WAN = rozhrani s vychozi cestou (ip route), at se jmenuje jakkoli (wan, wwan, pppoe-wan…); zalozne podle jmena
    wan_zone_names = [z["name"] for z in zones if route_dev and route_dev in z["devices"]]
    wan_zone_names += [z["name"] for z in zones if any(n in WAN_NETS for n in z["networks"]) and z["name"] not in wan_zone_names]
    if not wan_zone_names:  # fw3 / bez fw4 print: zona s maskaradou je skoro vzdy WAN
        wan_zone_names = [z["name"] for z in zones if z["masq"]]
    wan_devs = ([route_dev] if route_dev else []) + [d for zn in wan_zone_names for d in zmap[zn]["devices"]]
    eff = ""
    for d in wan_devs:
        if d in dev_zones:
            eff = dev_zones[d][0]
            break
    if not eff:
        eff = next((z["name"] for z in zones if "wan" in z["networks"]), "") or (wan_zone_names[0] if wan_zone_names else "")
        if not eff and route_dev:
            eff = next((z["name"] for z in zones if route_dev in z["devices"]), "")
    wz = zmap.get(eff)
    if wz and wz["input"] == "ACCEPT":
        warns.insert(0, {"level": "crit", "key": "fw.w_wan_input", "args": {"zone": eff, "ports": "–"}})
    if wz and wz["forward"] == "ACCEPT":
        warns.insert(1 if warns and warns[0]["level"] == "crit" else 0,
                     {"level": "crit", "key": "fw.w_wan_forward", "args": {"zone": eff}})
    if wz and not any(zmap[z]["masq"] for z in wan_zone_names if z in zmap):
        warns.append({"level": "warn", "key": "fw.w_no_masq", "args": {"zone": eff}})
    for inc in includes:
        if not inc["fw4"]:
            warns.append({"level": "info", "key": "fw.w_include", "args": {"path": inc["path"]}})

    # ---- co je otevrene z internetu
    exposed = []
    for rd in redirects:
        if rd["enabled"] and rd["target"] == "DNAT" and rd["src"] in (eff, *wan_zone_names):
            exposed.append({"how": "redirect", "proto": "/".join(rd["proto"]), "ports": fmt_ports(rd["src_dport"]),
                            "to": f"{rd['dest_ip'] or 'router'}:{fmt_ports(rd['dest_port'])}", "name": rd["name"],
                            "dest": rd["dest"]})
    for r in rules:
        if r["enabled"] and r["target"] == "ACCEPT" and r["src"] in (eff, *wan_zone_names) and not r["src_ip"] \
                and (r["dest_port"] or r["dest"]) and r["proto"] != ["icmp"]:
            exposed.append({"how": "rule", "proto": "/".join(r["proto"]) or "tcp/udp", "ports": fmt_ports(r["dest_port"]) or "*",
                            "to": r["dest"] or "router", "name": r["name"], "dest": r["dest"]})
    dnat = [(rd["src_dport"], rd["proto"]) for rd in redirects if rd["enabled"] and rd["src"] in (eff, *wan_zone_names)]

    def rule_for(port: int, proto: str, targets: tuple[str, ...]):
        for r in rules:
            if not r["enabled"] or r["dest"] or r["src"] not in (eff, "*") or r["src_ip"] or r["src_mac"]:
                continue
            if r["target"] in targets and (not r["dest_port"] or port_in(port, r["dest_port"])) \
                    and (not r["proto"] or proto in r["proto"] or "all" in r["proto"]):
                return r
        return None

    services = []
    seen = set()
    public = {"0.0.0.0", "::", "*", *wanips}
    for l in listen:
        if l["addr"] not in public or (l["proto"], l["port"]) in seen:
            continue
        seen.add((l["proto"], l["port"]))
        if any(port_in(l["port"], sp) and (l["proto"] in pr or "all" in pr) for sp, pr in dnat):
            state = "dnat"
            r = None
        else:
            r = rule_for(l["port"], l["proto"], ("REJECT", "DROP"))
            a = rule_for(l["port"], l["proto"], ("ACCEPT",))
            if a and (not r or rules.index(a) < rules.index(r)):
                state, r = "allowed", a
            elif r:
                state = "blocked"
            else:
                state = "open" if wz and wz["input"] == "ACCEPT" else "closed"
        services.append({**l, "state": state, "rule": r["name"] if r else ""})
    services.sort(key=lambda s: ({"open": 0, "allowed": 1, "dnat": 2, "blocked": 3, "closed": 4}[s["state"]], s["port"]))
    open_svc = [s for s in services if s["state"] == "open"]
    if open_svc and warns and warns[0]["key"] == "fw.w_wan_input":
        warns[0]["args"]["ports"] = ", ".join(f"{s['port']}/{s['proto']} ({s['prog'] or '?'})" for s in open_svc)

    # ---- matice pruchodu (radek = odkud, sloupec = kam); "router" = vstup na router
    names = [z["name"] for z in zones]
    fw_pairs = {(f["src"], f["dest"]) for f in forwards if f["enabled"]}
    matrix = []
    for z in zones:
        row = {"zone": z, "cells": []}
        exc_in = [r["name"] for r in rules if r["enabled"] and r["src"] == z["name"] and not r["dest"]]
        row["cells"].append({"dest": "router", "ok": z["input"] == "ACCEPT", "policy": z["input"], "rules": exc_in})
        for d in names:
            if d == z["name"]:
                row["cells"].append({"dest": d, "ok": z["forward"] == "ACCEPT", "policy": z["forward"], "self": True})
            else:
                exc = [r["name"] for r in rules if r["enabled"] and r["src"] == z["name"] and r["dest"] == d]
                row["cells"].append({"dest": d, "ok": (z["name"], d) in fw_pairs, "rules": exc})
        matrix.append(row)
    if fwver != "fw4":
        warns.insert(0, {"level": "info", "key": "fw.w_fw3", "args": {}})
    return {"ts": time.time(), "cron": [x for x in sec.get("cron", []) if x.strip()],
            "now_str": "".join(sec.get("now", [])).strip(),
            "fwver": fwver, "route_dev": route_dev, "zones": zones, "forwards": forwards,
            "rules": rules, "redirects": redirects,
            "includes": includes, "nftd": nftd, "warns": warns, "exposed": exposed, "services": services,
            "matrix": matrix, "wan_zone": eff, "wan_zones": wan_zone_names, "wanips": wanips,
            "addrs": _addrs(sec.get("addr", [])), "me": _me(sec.get("me", [])),
            "error": "" if "end" in sec else "incomplete"}


def _addrs(lines: list[str]) -> list[dict]:
    out = []
    for x in lines:
        p = x.split()
        if len(p) >= 2 and "/" in p[1]:
            out.append({"dev": p[0], "cidr": p[1]})
    return out


def _me(lines: list[str]) -> dict:
    """$SSH_CLIENT: 'ip_klienta port_klienta port_serveru' – odkud se aplikace pripojuje."""
    p = " ".join(lines).split()
    return {"ip": p[0], "port": int(p[2])} if len(p) >= 3 and p[2].isdigit() else {}


# ---------------------------------------------------------------- demo

DEMO = """@@fwver
fw4
@@uci
firewall.cfg01e63d=defaults
firewall.cfg01e63d.input='REJECT'
firewall.cfg01e63d.output='ACCEPT'
firewall.cfg01e63d.forward='REJECT'
firewall.lan=zone
firewall.lan.name='lan'
firewall.lan.network='lan'
firewall.lan.input='ACCEPT'
firewall.lan.output='ACCEPT'
firewall.lan.forward='ACCEPT'
firewall.guest=zone
firewall.guest.name='guest'
firewall.guest.network='guest'
firewall.guest.input='REJECT'
firewall.guest.output='ACCEPT'
firewall.guest.forward='REJECT'
firewall.iot=zone
firewall.iot.name='iot'
firewall.iot.network='iot' 'guest'
firewall.iot.input='ACCEPT'
firewall.iot.output='ACCEPT'
firewall.iot.forward='REJECT'
firewall.wan=zone
firewall.wan.name='wan'
firewall.wan.network='wan' 'wan6'
firewall.wan.input='REJECT'
firewall.wan.output='ACCEPT'
firewall.wan.forward='REJECT'
firewall.wan.masq='1'
firewall.dmz=zone
firewall.dmz.name='dmz'
firewall.dmz.network='dmz'
firewall.dmz.input='REJECT'
firewall.dmz.output='ACCEPT'
firewall.dmz.forward='REJECT'
firewall.cfg0f01=forwarding
firewall.cfg0f01.src='lan'
firewall.cfg0f01.dest='wan'
firewall.cfg0f02=forwarding
firewall.cfg0f02.src='guest'
firewall.cfg0f02.dest='wan'
firewall.cfg0f03=forwarding
firewall.cfg0f03.src='iot'
firewall.cfg0f03.dest='wan'
firewall.cfg0f04=forwarding
firewall.cfg0f04.src='dmz'
firewall.cfg0f04.dest='wan'
firewall.cfg0r01=rule
firewall.cfg0r01.name='Allow-Ping'
firewall.cfg0r01.src='wan'
firewall.cfg0r01.proto='icmp'
firewall.cfg0r01.icmp_type='echo-request'
firewall.cfg0r01.target='ACCEPT'
firewall.cfg0r02=rule
firewall.cfg0r02.name='Allow-WireGuard'
firewall.cfg0r02.src='wan'
firewall.cfg0r02.proto='udp'
firewall.cfg0r02.dest_port='51820'
firewall.cfg0r02.target='ACCEPT'
firewall.cfg0r03=rule
firewall.cfg0r03.name='Guest-DHCP-DNS'
firewall.cfg0r03.src='guest'
firewall.cfg0r03.proto='tcp' 'udp'
firewall.cfg0r03.dest_port='53 67'
firewall.cfg0r03.target='ACCEPT'
firewall.cfg0r04=rule
firewall.cfg0r04.name='LAN-to-DMZ-web'
firewall.cfg0r04.src='lan'
firewall.cfg0r04.dest='dmz'
firewall.cfg0r04.proto='tcp'
firewall.cfg0r04.dest_port='80 443'
firewall.cfg0r04.target='ACCEPT'
firewall.cfg0r05=rule
firewall.cfg0r05.name='Kids-evening'
firewall.cfg0r05.src='lan'
firewall.cfg0r05.dest='wan'
firewall.cfg0r05.src_mac='02:00:00:00:01:01' '02:00:00:00:01:02'
firewall.cfg0r05.start_time='21:00:00'
firewall.cfg0r05.stop_time='06:30:00'
firewall.cfg0r05.target='REJECT'
firewall.cfg0r06=rule
firewall.cfg0r06.name='Block-old-camera'
firewall.cfg0r06.src='iot'
firewall.cfg0r06.dest='wan'
firewall.cfg0r06.src_ip='192.168.10.31'
firewall.cfg0r06.target='DROP'
firewall.cfg0r06.enabled='0'
firewall.cfg0d01=redirect
firewall.cfg0d01.name='Web server'
firewall.cfg0d01.src='wan'
firewall.cfg0d01.src_dport='443'
firewall.cfg0d01.dest='dmz'
firewall.cfg0d01.dest_ip='192.168.100.7'
firewall.cfg0d01.dest_port='443'
firewall.cfg0d01.proto='tcp'
firewall.cfg0d01.target='DNAT'
firewall.cfg0d02=redirect
firewall.cfg0d02.name='Web server HTTP'
firewall.cfg0d02.src='wan'
firewall.cfg0d02.src_dport='80'
firewall.cfg0d02.dest='dmz'
firewall.cfg0d02.dest_ip='192.168.100.7'
firewall.cfg0d02.dest_port='80'
firewall.cfg0d02.proto='tcp'
firewall.cfg0d02.target='DNAT'
firewall.cfg0d03=redirect
firewall.cfg0d03.name='Old game server'
firewall.cfg0d03.src='wan'
firewall.cfg0d03.src_dport='27015'
firewall.cfg0d03.dest='lan'
firewall.cfg0d03.dest_ip='192.168.1.50'
firewall.cfg0d03.proto='udp'
firewall.cfg0d03.target='DNAT'
firewall.cfg0d03.enabled='0'
@@dispatch
		iifname "br-lan" jump input_lan comment "!fw4: Handle lan IPv4/IPv6 input traffic"
		iifname "br-guest" jump input_guest comment "!fw4: Handle guest IPv4/IPv6 input traffic"
		iifname { "br-iot", "br-guest" } jump input_iot comment "!fw4: Handle iot IPv4/IPv6 input traffic"
		iifname "eth0" jump input_wan comment "!fw4: Handle wan IPv4/IPv6 input traffic"
		iifname "br-dmz" jump input_dmz comment "!fw4: Handle dmz IPv4/IPv6 input traffic"
@@listen
Active Internet connections (only servers)
Proto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name
tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      1201/dropbear
tcp        0      0 0.0.0.0:80              0.0.0.0:*               LISTEN      1345/uhttpd
tcp        0      0 0.0.0.0:443             0.0.0.0:*               LISTEN      1345/uhttpd
tcp        0      0 0.0.0.0:1883            0.0.0.0:*               LISTEN      1502/mosquitto
tcp        0      0 192.168.1.1:53          0.0.0.0:*               LISTEN      2400/dnsmasq
udp        0      0 0.0.0.0:51820           0.0.0.0:*                           -
udp        0      0 0.0.0.0:67              0.0.0.0:*                           2400/dnsmasq
@@route
dev eth0
@@wanip
203.0.113.10
@@nftd
10-custom-filter-chains.nft
README
@@addr
lo 127.0.0.1/8
br-lan 192.168.1.1/24
br-guest 192.168.30.1/24
br-iot 192.168.10.1/24
br-dmz 192.168.100.1/24
eth0 203.0.113.10/24
@@me
192.168.1.11 51234 22
@@end
"""


def toggle_cmds(sid: str, enable: bool) -> list[str]:
    return [f"{{ uci -q delete firewall.{sid}.enabled || true; }}" if enable else f"uci set firewall.{sid}.enabled=0"]


RELOAD = ["/etc/init.d/firewall reload"]
CHECK = "fw4 -q check"
