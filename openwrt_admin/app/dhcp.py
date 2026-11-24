"""DHCP na routeru (dnsmasq): rozsahy podle siti, staticke zapujcky, aktualni zapujcky.

Cteni: jeden prikaz READ_CMD (uci -X = skutecna jmena anonymnich sekci, napr. cfg0a1b2c – stabilni i kdyz se
mezi nactenim a zapisem neco prida). Zapis: uci prikazy -> nahled -> Pouzit -> uci commit dhcp + dnsmasq reload,
pri chybe uci revert. IPv6 (odhcpd) se nemeni.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
import time

READ_CMD = r"""echo @@uci; uci -X -q show dhcp
echo @@net; uci -q show network | grep -E '^network\.[A-Za-z0-9_]+(=interface|\.(proto|ipaddr|netmask|device)=)'
echo @@leases; cat /tmp/dhcp.leases 2>/dev/null
echo @@apknew; ls /etc/config/*.apk-new /etc/config/*-opkg 2>/dev/null
echo @@resolv; cat /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf.auto 2>/dev/null
echo @@wan; uci -q show network.wan
echo @@now; date +%s
echo @@end
"""

NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
LEASETIME_RE = re.compile(r"^(\d{1,6}[smhdw]?|infinite)$")
SECTION_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
NET_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


# ---------------------------------------------------------------- uci parser

def _vals(raw: str) -> str | list[str]:
    """uci show hodnota: 'a' nebo 'a' 'b' (list) -> str / list."""
    parts = re.findall(r"'((?:[^']|'\\'')*)'", raw)
    parts = [p.replace("'\\''", "'") for p in parts]
    if not parts:
        return raw.strip()
    return parts[0] if len(parts) == 1 else parts


def parse_uci(text: str, pkg: str) -> dict[str, dict]:
    """uci show <pkg> -> {sekce: {"_type": typ, "_order": n, volba: hodnota}} (v poradi souboru)."""
    out: dict[str, dict] = {}
    for line in (text or "").splitlines():
        m = re.match(rf"^{re.escape(pkg)}\.([^.=]+)(?:\.([^=]+))?=(.*)$", line.strip())
        if not m:
            continue
        sec, opt, raw = m.groups()
        if opt is None:
            out.setdefault(sec, {})["_type"] = raw.strip().strip("'")
            out[sec]["_order"] = len(out)
        else:
            out.setdefault(sec, {"_type": "", "_order": len(out)})[opt] = _vals(raw)
    return out


def _first(v) -> str:
    return (v[0] if isinstance(v, list) and v else v) or ""


# ---------------------------------------------------------------- site a rozsahy

def networks(net_uci: dict[str, dict]) -> dict[str, dict]:
    """Rozhrani se statickou IPv4: {nazev: {net (IPv4Network), ip (adresa routeru), proto}}."""
    out = {}
    for name, o in net_uci.items():
        if o.get("_type") != "interface" or name == "loopback":
            continue
        raw = o.get("ipaddr")
        ips = raw if isinstance(raw, list) else ([raw] if raw else [])
        for ip in ips:
            try:
                if "/" in ip:
                    iface = ipaddress.ip_interface(ip)
                else:
                    iface = ipaddress.ip_interface(f"{ip}/{o.get('netmask') or '255.255.255.0'}")
            except ValueError:
                continue
            if iface.version == 4:
                out[name] = {"net": iface.network, "ip": iface.ip, "proto": o.get("proto", "")}
                break
    return out


def parse_leases(text: str) -> list[dict]:
    """/tmp/dhcp.leases: '<expiry> <mac> <ip> <hostname> <client-id>'."""
    out = []
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 4 and MAC_RE.match(p[1].lower()):
            out.append({"expires": int(p[0]) if p[0].isdigit() else 0, "mac": p[1].lower(), "ip": p[2],
                        "hostname": "" if p[3] == "*" else p[3]})
    return sorted(out, key=lambda l: _ipkey(l["ip"]))


def _ipkey(ip: str):
    try:
        return (0, int(ipaddress.ip_address(ip)))
    except ValueError:
        return (1, 0)


def norm_mac(s: str) -> str | None:
    s = (s or "").strip().lower().replace("-", ":")
    if re.fullmatch(r"[0-9a-f]{12}", s):
        s = ":".join(s[i:i + 2] for i in range(0, 12, 2))
    return s if MAC_RE.match(s) else None


def _int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def pool_range(net: ipaddress.IPv4Network, start: int, limit: int) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address] | None:
    """Dynamicky rozsah jako v OpenWrt: sit + start .. + start + limit - 1, oriznuto pred broadcast."""
    if limit <= 0 or start <= 0:
        return None
    first = net.network_address + start
    last_i = min(int(net.network_address) + start + limit - 1, int(net.broadcast_address) - 1)
    if first not in net or int(first) > last_i:
        return None
    return first, ipaddress.IPv4Address(last_i)


def build(text: str) -> dict:
    """Vystup READ_CMD -> model pro stranku a validace."""
    sec: dict[str, list[str]] = {}
    cur = None
    for line in (text or "").splitlines():
        if re.fullmatch(r"@@[a-z]+", line.strip()):
            cur = line.strip()[2:]
            sec.setdefault(cur, [])
        elif cur:
            sec[cur].append(line)
    uci = parse_uci("\n".join(sec.get("uci", [])), "dhcp")
    nets = networks(parse_uci("\n".join(sec.get("net", [])), "network"))
    leases = parse_leases("\n".join(sec.get("leases", [])))
    now = _int("".join(sec.get("now", [])).strip(), int(time.time())) or int(time.time())

    def net_of(ip: str) -> str:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return ""
        best = ""
        for n, v in nets.items():
            if a in v["net"] and (not best or v["net"].prefixlen > nets[best]["net"].prefixlen):
                best = n
        return best

    hosts = []
    for sid, o in sorted(uci.items(), key=lambda kv: kv[1]["_order"]):
        if o["_type"] != "host":
            continue
        macs = o.get("mac") or ""
        macs = macs if isinstance(macs, list) else macs.split()
        ip = _first(o.get("ip"))
        hosts.append({"id": sid, "name": _first(o.get("name")), "macs": [m.lower() for m in macs], "ip": ip,
                      "dns": str(o.get("dns", "")) == "1", "leasetime": _first(o.get("leasetime")),
                      "net": net_of(ip) if ip else ""})
    pools = []
    served = set()
    for sid, o in sorted(uci.items(), key=lambda kv: kv[1]["_order"]):
        if o["_type"] != "dhcp":
            continue
        iface = _first(o.get("interface")) or sid
        n = nets.get(iface)
        ignore = str(o.get("ignore", "")) == "1" or o.get("dhcpv4") == "disabled"
        start, limit = _int(o.get("start"), 100), _int(o.get("limit"), 150)
        rng = pool_range(n["net"], start, limit) if n else None
        p = {"id": sid, "iface": iface, "start": start, "limit": limit, "leasetime": _first(o.get("leasetime")) or "12h",
             "ignore": ignore, "subnet": str(n["net"]) if n else "", "router_ip": str(n["ip"]) if n else "",
             "first": str(rng[0]) if rng else "", "last": str(rng[1]) if rng else "", "size": 0,
             "dyn": 0, "res_in": 0, "free": 0, "wan": iface.startswith("wan")}
        if rng and not ignore:
            served.add(iface)
            lo, hi = int(rng[0]), int(rng[1])
            p["size"] = hi - lo + 1
            res_ips = {h["ip"] for h in hosts if h["ip"]}
            inr = lambda ip: _in(ip, lo, hi)  # noqa: E731
            p["res_in"] = sum(1 for ip in res_ips if inr(ip))
            p["dyn"] = sum(1 for l in leases if inr(l["ip"]) and l["ip"] not in res_ips and l["expires"] > now - 60)
            p["free"] = max(0, p["size"] - p["res_in"] - p["dyn"])
        pools.append(p)
    pooled = {p["iface"] for p in pools}
    no_dhcp = [{"iface": k, "subnet": str(v["net"]), "router_ip": str(v["ip"])} for k, v in nets.items()
               if k not in pooled and v["proto"] == "static" and not k.startswith("wan") and v["net"].is_private]
    domains = [{"id": sid, "name": _first(o.get("name")), "ip": _first(o.get("ip"))}
               for sid, o in sorted(uci.items(), key=lambda kv: kv[1]["_order"]) if o["_type"] == "domain"]
    cnames = [{"id": sid, "cname": _first(o.get("cname")), "target": _first(o.get("target"))}
              for sid, o in sorted(uci.items(), key=lambda kv: kv[1]["_order"]) if o["_type"] == "cname"]
    dm = next((o for _, o in sorted(uci.items(), key=lambda kv: kv[1]["_order"]) if o["_type"] == "dnsmasq"), {})
    servers = dm.get("server") or []
    servers = servers if isinstance(servers, list) else [servers]
    upstream = []
    for line in sec.get("resolv", []):
        p_ = line.split()
        if len(p_) >= 2 and p_[0] == "nameserver" and p_[1] not in upstream:
            upstream.append(p_[1])
    rebind_ok = dm.get("rebind_domain") or []
    wan = parse_uci("\n".join(sec.get("wan", [])), "network").get("wan", {})
    wdns = wan.get("dns") or []
    wan_info = {"exists": bool(wan), "proto": _first(wan.get("proto")), "dns": wdns if isinstance(wdns, list) else [wdns],
                "peerdns": str(wan.get("peerdns", "1")) != "0"}
    dnsmasq = {"id": next((sid for sid, o in uci.items() if o is dm), ""), "domain": _first(dm.get("domain")),
               "local": _first(dm.get("local")), "noresolv": str(dm.get("noresolv", "")) == "1",
               "servers": [x for x in servers if not x.startswith("/")],
               "forwards": [x for x in servers if x.startswith("/")],
               "rebind": str(dm.get("rebind_protection", "1")) == "1",
               "rebind_ok": rebind_ok if isinstance(rebind_ok, list) else [rebind_ok],
               "expandhosts": str(dm.get("expandhosts", "")) == "1", "localservice": str(dm.get("localservice", "")) == "1",
               "upstream": upstream}
    for h in hosts:
        p = next((x for x in pools if x["iface"] == h["net"] and x["first"]), None)
        h["in_pool"] = bool(p and _in(h["ip"], int(ipaddress.ip_address(p["first"])), int(ipaddress.ip_address(p["last"]))))
    return {"ts": time.time(), "now": now, "uci": uci, "nets": {k: {"subnet": str(v["net"]), "ip": str(v["ip"]),
                                                                       "proto": v["proto"]} for k, v in nets.items()},
            "_nets": nets, "hosts": hosts, "pools": pools, "no_dhcp": no_dhcp, "leases": leases, "served": served,
            "domains": domains, "cnames": cnames, "dnsmasq": dnsmasq, "wan": wan_info,
            "apknew": [x.strip() for x in sec.get("apknew", []) if x.strip()], "error": "" if "end" in sec else "incomplete"}


def _in(ip: str, lo: int, hi: int) -> bool:
    try:
        return lo <= int(ipaddress.ip_address(ip)) <= hi
    except ValueError:
        return False


# ---------------------------------------------------------------- volna IP

def suggest_ip(model: dict, iface: str, taken_extra: set[str] | None = None) -> str:
    """Prvni volna adresa pod dynamickym rozsahem (shora dolu), jinak nad nim; ne router, rezervace,
    zapujcky, ARP ani mistni jmena."""
    n = model["_nets"].get(iface)
    if not n:
        return ""
    taken = {h["ip"] for h in model["hosts"]} | {l["ip"] for l in model["leases"]} | \
        {d["ip"] for d in model["domains"]} | {str(n["ip"])} | (taken_extra or set())
    p = next((x for x in model["pools"] if x["iface"] == iface and x["first"] and not x["ignore"]), None)
    hosts = list(n["net"].hosts())
    if p:
        lo, hi = int(ipaddress.ip_address(p["first"])), int(ipaddress.ip_address(p["last"]))
        below = [a for a in reversed(hosts) if int(a) < lo]
        above = [a for a in hosts if int(a) > hi]
        cand = below + above
    else:
        cand = list(reversed(hosts))
    for a in cand:
        if str(a) not in taken:
            return str(a)
    return ""


# ---------------------------------------------------------------- validace a prikazy

def check_host(model: dict, name: str, mac: str, ip: str, host_id: str | None, t) -> tuple[list[str], list[str]]:
    """(chyby, varovani) pro statickou zapujcku."""
    errs, warns = [], []
    if not NAME_RE.match(name or ""):
        errs.append(t("dhcp.err_name"))
    if not mac:
        errs.append(t("dhcp.err_mac"))
    try:
        a = ipaddress.ip_address(ip)
        if a.version != 4:
            raise ValueError
    except ValueError:
        errs.append(t("dhcp.err_ip"))
        return errs, warns
    net = next((k for k, v in model["_nets"].items() if a in v["net"]), "")
    if not net:
        errs.append(t("dhcp.err_ip_net"))
        return errs, warns
    n = model["_nets"][net]
    if a in (n["net"].network_address, n["net"].broadcast_address) or a == n["ip"]:
        errs.append(t("dhcp.err_ip_reserved", ip=ip))
    for h in model["hosts"]:
        if h["id"] == host_id:
            continue
        if h["ip"] == ip:
            errs.append(t("dhcp.err_ip_taken", ip=ip, name=h["name"] or h["id"]))
        if mac and mac in h["macs"]:
            errs.append(t("dhcp.err_mac_taken", mac=mac, name=h["name"] or h["id"]))
        if name and h["name"].lower() == name.lower():
            warns.append(t("dhcp.warn_name_dup", name=name))
    for l in model["leases"]:
        if l["ip"] == ip and l["mac"] != mac and l["expires"] > model["now"]:
            warns.append(t("dhcp.warn_ip_leased", ip=ip, mac=l["mac"], name=l["hostname"] or "?"))
    for d in model["domains"]:
        if d["ip"] == ip:
            warns.append(t("dhcp.warn_ip_domain", ip=ip, name=d["name"]))
    if net not in model["served"]:
        warns.append(t("dhcp.warn_no_dhcp", net=net))
    p = next((x for x in model["pools"] if x["iface"] == net and x["first"] and not x["ignore"]), None)
    if p and _in(ip, int(ipaddress.ip_address(p["first"])), int(ipaddress.ip_address(p["last"]))):
        warns.append(t("dhcp.warn_in_pool", ip=ip, first=p["first"], last=p["last"],
                       free=suggest_ip(model, net, {ip}) or "–"))
    return errs, warns


def q(v: str) -> str:
    return shlex.quote(str(v))


def host_add_cmds(name: str, mac: str, ip: str, dns: bool) -> list[str]:
    cmds = ["s=$(uci add dhcp host)", f'uci set "dhcp.$s.name"={q(name)}', f'uci set "dhcp.$s.mac"={q(mac.upper())}',
            f'uci set "dhcp.$s.ip"={q(ip)}']
    if dns:
        cmds.append('uci set "dhcp.$s.dns=1"')
    return cmds


def host_edit_cmds(h: dict, name: str, mac: str, ip: str, dns: bool) -> list[str]:
    sid = h["id"]
    cmds = []
    if name != h["name"]:
        cmds.append(f"uci set dhcp.{sid}.name={q(name)}")
    if mac and [mac] != h["macs"]:
        cmds.append(f"uci set dhcp.{sid}.mac={q(mac.upper())}")
    if ip != h["ip"]:
        cmds.append(f"uci set dhcp.{sid}.ip={q(ip)}")
    if dns != h["dns"]:
        cmds.append(f"uci set dhcp.{sid}.dns=1" if dns else f"{{ uci -q delete dhcp.{sid}.dns || true; }}")
    return cmds


def host_del_cmds(h: dict) -> list[str]:
    return [f"uci delete dhcp.{h['id']}"]


def check_pool(model: dict, iface: str, start: int, limit: int, leasetime: str, t) -> list[str]:
    errs = []
    n = model["_nets"].get(iface)
    if not n:
        return [t("dhcp.err_net")]
    size = n["net"].num_addresses
    if start < 1 or limit < 1 or start + limit > size - 1:
        errs.append(t("dhcp.err_range", max=size - 2))
    if not LEASETIME_RE.match(leasetime or ""):
        errs.append(t("dhcp.err_leasetime"))
    else:
        m = re.match(r"^(\d+)([smhdw]?)$", leasetime)
        if m and int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)] < 120:
            errs.append(t("dhcp.err_leasetime"))
    return errs


def pool_cmds(p: dict | None, iface: str, start: int, limit: int, leasetime: str, enabled: bool) -> list[str]:
    if p is None:  # sit bez DHCP sekce -> nova (pojmenovana podle site, jako v LuCI)
        return [f"uci set dhcp.{iface}=dhcp", f"uci set dhcp.{iface}.interface={q(iface)}",
                f"uci set dhcp.{iface}.start={start}", f"uci set dhcp.{iface}.limit={limit}",
                f"uci set dhcp.{iface}.leasetime={q(leasetime)}"]
    sid, cmds = p["id"], []
    if start != p["start"]:
        cmds.append(f"uci set dhcp.{sid}.start={start}")
    if limit != p["limit"]:
        cmds.append(f"uci set dhcp.{sid}.limit={limit}")
    if leasetime != p["leasetime"]:
        cmds.append(f"uci set dhcp.{sid}.leasetime={q(leasetime)}")
    if enabled == p["ignore"]:
        cmds.append(f"{{ uci -q delete dhcp.{sid}.ignore || true; }}" if enabled else f"uci set dhcp.{sid}.ignore=1")
    return cmds


def apply_script(cmds: list[str], reverted: str) -> str:
    """uci zmeny -> commit + reload dnsmasq; pri chybe revert (nic se nezapise)."""
    return (" && ".join(cmds) + " && uci commit dhcp && /etc/init.d/dnsmasq reload && echo OK"
            f" || {{ uci revert dhcp; echo {q(reverted)}; exit 1; }}")


# ---------------------------------------------------------------- demo

DEMO_UCI = {
    "cfg01411c": {"_type": "dnsmasq", "domain": "lan", "local": "/lan/", "rebind_protection": "1", "expandhosts": "1",
                  "localservice": "1", "server": "/firma.example/10.8.0.1"},
    "lan": {"_type": "dhcp", "interface": "lan", "start": "100", "limit": "150", "leasetime": "12h"},
    "wan": {"_type": "dhcp", "interface": "wan", "ignore": "1"},
    "guest": {"_type": "dhcp", "interface": "guest", "start": "100", "limit": "50", "leasetime": "1h"},
    "iot": {"_type": "dhcp", "interface": "iot", "start": "50", "limit": "100", "leasetime": "12h"},
    "cfg0a1b01": {"_type": "host", "name": "nas", "mac": "02:00:00:00:10:01", "ip": "192.168.1.10", "dns": "1"},
    "cfg0a1b02": {"_type": "host", "name": "printer", "mac": "02:00:00:00:10:03", "ip": "192.168.1.12", "dns": "1"},
    "cfg0a1b03": {"_type": "host", "name": "tv-living", "mac": "02:00:00:00:20:03", "ip": "192.168.1.150", "dns": "1"},
    "cfg0a1b04": {"_type": "host", "name": "esp-teplomer", "mac": "02:00:00:00:20:04", "ip": "192.168.10.20"},
    "cfg0c0d01": {"_type": "domain", "name": "nas.home", "ip": "192.168.1.10"},
    "cfg0c0d02": {"_type": "domain", "name": "grafana", "ip": "192.168.1.10"},
    "cfg0c0d03": {"_type": "domain", "name": "web.example.org", "ip": "192.168.100.7"},
    "cfg0e0f01": {"_type": "cname", "cname": "jellyfin.lan", "target": "nas.lan"},
}
DEMO_NET = """network.lan=interface
network.lan.proto='static'
network.lan.ipaddr='192.168.1.1'
network.lan.netmask='255.255.255.0'
network.guest=interface
network.guest.proto='static'
network.guest.ipaddr='192.168.30.1/24'
network.iot=interface
network.iot.proto='static'
network.iot.ipaddr='192.168.10.1'
network.iot.netmask='255.255.255.0'
network.dmz=interface
network.dmz.proto='static'
network.dmz.ipaddr='192.168.100.1'
network.dmz.netmask='255.255.255.0'
network.wan=interface
network.wan.proto='dhcp'"""


def to_uci(model: dict[str, dict]) -> str:
    out = []
    for sid, o in model.items():
        out.append(f"dhcp.{sid}={o['_type']}")
        for k, v in o.items():
            if not k.startswith("_"):
                vals = v if isinstance(v, list) else [v]
                out.append(f"dhcp.{sid}.{k}=" + " ".join(f"'{x}'" for x in vals))
    return "\n".join(out)


def demo_text(model: dict[str, dict], leases: dict[str, dict], wan_dns: list[str] | None = None) -> str:
    now = int(time.time())
    ls = "\n".join(f"{now + 3600 + i * 60} {mac} {l['ip']} {l.get('hostname') or '*'} 01:{mac}"
                   for i, (mac, l) in enumerate(leases.items()) if l.get("ip"))
    return (f"@@uci\n{to_uci(model)}\n@@net\n{DEMO_NET}\n@@leases\n{ls}\n@@apknew\n/etc/config/dhcp.apk-new\n/etc/config/luci.apk-new\n/etc/config/uhttpd.apk-new\n"
            f"@@resolv\n" + "\n".join(f"nameserver {x}" for x in (wan_dns or ["203.0.113.53"])) +
            "\n@@wan\nnetwork.wan=interface\nnetwork.wan.proto='dhcp'\n" +
            (("network.wan.peerdns='0'\nnetwork.wan.dns=" + " ".join(f"'{x}'" for x in wan_dns) + "\n") if wan_dns else "") +
            f"@@now\n{now}\n@@end\n")


def demo_apply(model: dict[str, dict], info: dict):
    op = info.get("op")
    if op == "host_add":
        sid = f"cfg{len(model) + 0x100:06x}"
        model[sid] = {"_type": "host", "name": info["name"], "mac": info["mac"].upper(), "ip": info["ip"],
                      **({"dns": "1"} if info.get("dns") else {})}
    elif op == "host_edit" and info["id"] in model:
        o = model[info["id"]]
        o.update(name=info["name"], ip=info["ip"], mac=info["mac"].upper())
        if info.get("dns"):
            o["dns"] = "1"
        else:
            o.pop("dns", None)
    elif op in ("host_del", "dom_del", "cname_del"):
        model.pop(info["id"], None)
    elif op == "dom_add":
        model[f"cfg{len(model) + 0x200:06x}"] = {"_type": "domain", "name": info["name"], "ip": info["ip"]}
    elif op == "dom_edit" and info["id"] in model:
        model[info["id"]].update(name=info["name"], ip=info["ip"])
    elif op in ("dns_up", "fwd_add", "fwd_del"):
        dm = next(o for o in model.values() if o["_type"] == "dnsmasq")
        if info["servers"] or info["forwards"]:
            dm["server"] = info["servers"] + info["forwards"]
        else:
            dm.pop("server", None)
        if info["servers"]:
            dm["noresolv"] = "1"
        else:
            dm.pop("noresolv", None)
    elif op == "cname_add":
        model[f"cfg{len(model) + 0x300:06x}"] = {"_type": "cname", "cname": info["cname"], "target": info["target"]}
    elif op == "cname_edit" and info["id"] in model:
        model[info["id"]].update(cname=info["cname"], target=info["target"])
    elif op == "pool":
        o = model.setdefault(info["sid"], {"_type": "dhcp", "interface": info["iface"]})
        o.update(start=str(info["start"]), limit=str(info["limit"]), leasetime=info["leasetime"])
        if info["enabled"]:
            o.pop("ignore", None)
        else:
            o["ignore"] = "1"
