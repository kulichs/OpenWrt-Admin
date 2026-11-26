"""Presmerovani portu (fw4 `config redirect`, DNAT) a pojistka proti odriznuti spravy.

Nove presmerovani dostane pojmenovanou sekci firewall.owa_fwd_<id> (stabilni – anonymni cfg… se pri mazani
jinych sekci precisluji). Upravit a smazat jde i presmerovani vytvorena jinde (LuCI), adresuji se jmenem sekce
z `uci -X show`. Cilova zona se odvodi z IP (sit routeru -> rozhrani -> zona podle `fw4 print`).

Pojistka proti odriznuti: aplikace vi, odkud se k routeru pripojuje (SSH_CLIENT pri cteni), a pred zmenou
pravidla / presmerovani zkontroluje, jestli by zmena nezavrela nebo neprevedla jeji SSH. Takova zmena se
v nahledu ukaze cervene; ulozeni je stejne pod pojistkou (bez potvrzeni se router sam vrati).
"""
from __future__ import annotations

import ipaddress
import re
import secrets

from .firewall import _list, port_in, ports_of

PREFIX = "owa_fwd_"
SID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
NAME_RE = re.compile(r"^[\w .:+()/-]{1,40}$")
PORT_RE = re.compile(r"^(\d{1,5})(?:\s*[-:]\s*(\d{1,5}))?$")
PROTOS = {"tcp": ["tcp"], "udp": ["udp"], "tcpudp": ["tcp", "udp"]}

# sluzby, ktere se z internetu casto napadaji – presmerovani se povoli, ale s varovanim
RISKY = {21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 135: "RPC", 139: "SMB", 161: "SNMP",
         445: "SMB", 1433: "MSSQL", 1883: "MQTT", 2375: "Docker API", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
         5900: "VNC", 6379: "Redis", 8080: "HTTP proxy/admin", 9200: "Elasticsearch", 11211: "memcached",
         27017: "MongoDB"}


def new_sid() -> str:
    return PREFIX + secrets.token_hex(4)


def parse_port(s: str) -> tuple[int, int] | None:
    """'8443' nebo '5000-5010' -> (od, do); None pri chybe."""
    m = PORT_RE.match((s or "").strip())
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2) or a)
    if not (1 <= a <= b <= 65535):
        return None
    return a, b


def fmt_range(r: tuple[int, int]) -> str:
    return str(r[0]) if r[0] == r[1] else f"{r[0]}-{r[1]}"


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def norm_src(s: str) -> str | None:
    """Omezeni zdroje: prazdne, IP nebo sit (CIDR). None pri chybe."""
    s = (s or "").strip()
    if not s:
        return ""
    try:
        n = ipaddress.ip_network(s, strict=False)
    except ValueError:
        return None
    if n.version != 4:
        return None
    return str(n.network_address) if n.prefixlen == 32 else str(n)


# ---------------------------------------------------------------- site routeru

def subnets(fw: dict) -> list[dict]:
    """[{dev, ip, net, zone}] z `ip -4 -o addr` + zona rozhrani podle fw4 print."""
    zone_of_dev: dict[str, str] = {}
    for z in fw.get("zones", []):
        for d in z.get("devices", []):
            zone_of_dev.setdefault(d, z["name"])
    out = []
    for a in fw.get("addrs", []):
        try:
            iface = ipaddress.ip_interface(a["cidr"])
        except ValueError:
            continue
        out.append({"dev": a["dev"], "ip": str(iface.ip), "net": iface.network, "zone": zone_of_dev.get(a["dev"], "")})
    return out


def zone_of_ip(fw: dict, ip: str) -> tuple[str, dict | None]:
    """(zona, podsit) pro IP; ("", None) kdyz IP nelezi v zadne siti routeru."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "", None
    best = None
    for s in subnets(fw):
        if addr in s["net"] and (best is None or s["net"].prefixlen > best["net"].prefixlen):
            best = s
    return (best["zone"], best) if best else ("", None)


# ---------------------------------------------------------------- kontrola

def check(fw: dict, form: dict, edit_id: str | None, static_ips: set[str]) -> tuple[list[tuple], list[tuple], dict]:
    """(chyby, varovani, data). Chyby/varovani = (klic, args) pro preklad."""
    errs: list[tuple] = []
    warns: list[tuple] = []
    name = (form.get("name") or "").strip()
    proto_key = form.get("proto") or "tcp"
    ext = parse_port(form.get("src_dport") or "")
    ip = (form.get("dest_ip") or "").strip()
    int_raw = (form.get("dest_port") or "").strip()
    intp = parse_port(int_raw) if int_raw else ext
    src = norm_src(form.get("src_ip") or "")
    wan = fw.get("wan_zone") or ""
    if not NAME_RE.match(name):
        errs.append(("fwd.err_name", {}))
    if proto_key not in PROTOS:
        errs.append(("msg.bad_request", {}))
    if not ext:
        errs.append(("fwd.err_port", {"v": form.get("src_dport") or ""}))
    if int_raw and not intp:
        errs.append(("fwd.err_port", {"v": int_raw}))
    if ext and intp and (ext[1] - ext[0]) != (intp[1] - intp[0]):
        errs.append(("fwd.err_range", {}))
    if src is None:
        errs.append(("fwd.err_src", {"v": form.get("src_ip") or ""}))
    if not wan:
        errs.append(("blk.err_nowan", {}))
    zone, sub = zone_of_ip(fw, ip)
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        errs.append(("fwd.err_ip", {"v": ip}))
        zone = ""
    else:
        if not zone:
            errs.append(("fwd.err_ip_net", {"ip": ip}))
        elif zone in (wan, *fw.get("wan_zones", [])):
            errs.append(("fwd.err_ip_wan", {"ip": ip, "zone": zone}))
    data = {"name": name, "proto": PROTOS.get(proto_key, ["tcp"]), "src": wan, "src_dport": fmt_range(ext) if ext else "",
            "dest": zone, "dest_ip": ip, "dest_port": fmt_range(intp) if intp else "", "src_ip": src or ""}
    if errs:
        return errs, warns, data
    # stejny vnejsi port uz presmerovany jinam (prvni pravidlo vyhrava, druhe by nefungovalo)
    for rd in fw.get("redirects", []):
        if rd["id"] == edit_id or not rd["enabled"] or rd["target"] != "DNAT":
            continue
        if rd["src"] not in (wan, *fw.get("wan_zones", [])):
            continue
        if not set(rd["proto"]) & set(data["proto"]) and "all" not in rd["proto"]:
            continue
        if any(_overlap(ext, r) for r in ports_of(rd["src_dport"])):
            if rd["src_ip"] and src and rd["src_ip"] != src:
                continue
            errs.append(("fwd.err_dup", {"name": rd["name"], "port": rd["src_dport"]}))
    if sub and ip == sub["ip"]:
        warns.append(("fwd.w_router", {"ip": ip}))
    for s in fw.get("services", []):
        if s["proto"] in data["proto"] and ext[0] <= s["port"] <= ext[1] and s["state"] != "dnat":
            warns.append(("fwd.w_router_svc", {"port": s["port"], "prog": s["prog"] or "?"}))
    risky = sorted({f"{p} ({RISKY[p]})" for p in RISKY if ext[0] <= p <= ext[1] or intp[0] <= p <= intp[1]})
    if risky and not src:
        warns.append(("fwd.w_risky", {"ports": ", ".join(risky)}))
    if ext[1] - ext[0] >= 100:
        warns.append(("fwd.w_wide", {"n": ext[1] - ext[0] + 1}))
    if ip not in static_ips and not (sub and ip == sub["ip"]):
        warns.append(("fwd.w_dynamic", {"ip": ip}))
    return errs, warns, data


# ---------------------------------------------------------------- prikazy

def _q(v: str) -> str:
    return "'" + str(v).replace("'", "'\\''") + "'"


def set_cmds(sid: str, d: dict, new: bool) -> list[str]:
    p = f"firewall.{sid}"
    cmds = [f"uci set {p}=redirect"] if new else []
    cmds += [f"uci set {p}.name={_q(d['name'])}", f"uci set {p}.src={_q(d['src'])}",
             f"uci set {p}.src_dport={_q(d['src_dport'])}", f"uci set {p}.dest={_q(d['dest'])}",
             f"uci set {p}.dest_ip={_q(d['dest_ip'])}", f"uci set {p}.dest_port={_q(d['dest_port'])}",
             f"uci set {p}.target='DNAT'"]
    if not new:
        cmds.append(f"{{ uci -q delete {p}.proto || true; }}")
    cmds += [f"uci add_list {p}.proto={_q(x)}" for x in d["proto"]]
    if d["src_ip"]:
        cmds.append(f"uci set {p}.src_ip={_q(d['src_ip'])}")
    elif not new:
        cmds.append(f"{{ uci -q delete {p}.src_ip || true; }}")
    return cmds


def delete_cmds(sid: str) -> list[str]:
    return [f"uci delete firewall.{sid}"]


def changed(old: dict, d: dict) -> bool:
    return any(str(old.get(k) or "") != str(d.get(k) or "") for k in ("name", "src_dport", "dest_ip", "dest_port", "src_ip")) \
        or sorted(old.get("proto") or []) != sorted(d["proto"])


# ---------------------------------------------------------------- pojistka proti odriznuti

def mgmt(fw: dict) -> dict:
    """Odkud se aplikace k routeru pripojuje: IP klienta, port SSH na routeru, zona klienta."""
    me = fw.get("me") or {}
    ip, port = me.get("ip", ""), me.get("port", 0)
    zone, _ = zone_of_ip(fw, ip) if ip else ("", None)
    return {"ip": ip, "port": port, "zone": zone}


def _covers(r: dict, port: int) -> bool:
    protos = r.get("proto") or []
    if protos and "tcp" not in protos and "all" not in protos:
        return False
    return not r.get("dest_port") or port_in(port, r["dest_port"])


def _src_hits(r: dict, mg: dict) -> bool:
    if r.get("src") not in (mg["zone"], "*"):
        return False
    if r.get("src_ip"):
        try:
            a = ipaddress.ip_address(mg["ip"])
            return any(a in ipaddress.ip_network(x, strict=False) for x in r["src_ip"] if not x.startswith("!"))
        except ValueError:
            return True
    return True


def lockout_rule(fw: dict, r: dict, enable: bool | None) -> list[tuple]:
    """Rizika zapnuti / vypnuti / smazani (enable=None) pravidla pro SSH aplikace."""
    mg = mgmt(fw)
    if not mg["zone"] or not mg["port"] or r.get("dest") or r.get("src_mac") and r.get("target") != "ACCEPT":
        return []
    if not _src_hits(r, mg) or not _covers(r, mg["port"]):
        return []
    zone = next((z for z in fw.get("zones", []) if z["name"] == mg["zone"]), None)
    policy_ok = bool(zone and zone["input"] == "ACCEPT")
    if r["target"] == "ACCEPT" and enable is not True and not policy_ok:
        return [("fwd.lock_accept", {"name": r["name"], "zone": mg["zone"], "port": mg["port"]})]
    if r["target"] in ("REJECT", "DROP") and enable is True:
        return [("fwd.lock_block", {"name": r["name"], "zone": mg["zone"], "port": mg["port"]})]
    return []


def lockout_redirect(fw: dict, d: dict) -> list[tuple]:
    """Presmerovani ze zony, odkud jde SSH aplikace, na port SSH by ho odvedlo jinam."""
    mg = mgmt(fw)
    if not mg["zone"] or not mg["port"] or d.get("src") not in (mg["zone"], "*"):
        return []
    if "tcp" not in (d.get("proto") or ["tcp"]) and "all" not in (d.get("proto") or []):
        return []
    if not port_in(mg["port"], d.get("src_dport") or ""):
        return []
    if d.get("src_ip"):
        try:
            if ipaddress.ip_address(mg["ip"]) not in ipaddress.ip_network(d["src_ip"], strict=False):
                return []
        except ValueError:
            pass
    return [("fwd.lock_redirect", {"port": mg["port"], "zone": mg["zone"]})]


def lockout_block(fw: dict, macs: list[str], arp: dict[str, str]) -> list[tuple]:
    """Blokace MAC, ze ktere se pripojuje aplikace (Home Assistant prijde o internet)."""
    mg = mgmt(fw)
    mac = (arp.get(mg["ip"]) or "").lower()
    return [("fwd.lock_ha_block", {"mac": mac.upper()})] if mac and mac in [m.lower() for m in macs] else []


def demo_lines(items: dict[str, dict | None], text: str) -> str:
    """Demo: prepsat / pridat / smazat sekce redirect v demo uci textu."""
    for sid, d in items.items():
        text = "\n".join(l for l in text.splitlines() if not re.match(rf"firewall\.{re.escape(sid)}[.=]", l)) + "\n"
        if d is None:
            continue
        lines = [f"firewall.{sid}=redirect", f"firewall.{sid}.name='{d['name']}'", f"firewall.{sid}.src='{d['src']}'",
                 f"firewall.{sid}.src_dport='{d['src_dport']}'", f"firewall.{sid}.dest='{d['dest']}'",
                 f"firewall.{sid}.dest_ip='{d['dest_ip']}'", f"firewall.{sid}.dest_port='{d['dest_port']}'",
                 f"firewall.{sid}.proto=" + " ".join(f"'{x}'" for x in d["proto"]), f"firewall.{sid}.target='DNAT'"]
        if d.get("src_ip"):
            lines.append(f"firewall.{sid}.src_ip='{d['src_ip']}'")
        if d.get("enabled") is False:
            lines.append(f"firewall.{sid}.enabled='0'")
        text = text.replace("@@dispatch", "\n".join(lines) + "\n@@dispatch", 1)
    return text


__all__ = ["check", "set_cmds", "delete_cmds", "lockout_rule", "lockout_redirect", "lockout_block", "mgmt",
           "zone_of_ip", "subnets", "demo_lines", "new_sid", "SID_RE", "RISKY", "_list"]
