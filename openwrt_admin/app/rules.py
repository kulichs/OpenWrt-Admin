"""Pravidla firewallu (fw4 `config rule`): nove, uprava, smazani a poradi.

Ve fw4 plati prvni pravidlo, ktere sedi – proto jde nove pravidlo dat na zacatek nebo na konec a existujici
posouvat nahoru / dolu (`uci reorder` na pozici sousedniho pravidla). Nova pravidla maji pojmenovanou sekci
owa_rule_<id>. Upravit jde jen pravidlo, jehoz volby formular zna (jinak jen zapnout/vypnout, posunout, smazat).
Kontroly: chybne vstupy, otevreni z internetu, ochrana proti odriznuti spravy, pravidlo bez ucinku a pravidlo
zastinene drivejsim.
"""
from __future__ import annotations

import ipaddress
import re
import secrets

from .firewall import port_in, ports_of
from .portfwd import RISKY, lockout_rule

PREFIX = "owa_rule_"
SID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
NAME_RE = re.compile(r"^[\w .:+()/,-]{1,50}$")
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
PORT_ITEM = re.compile(r"^(\d{1,5})(?:[-:](\d{1,5}))?$")
PROTOS = {"any": [], "tcp": ["tcp"], "udp": ["udp"], "tcpudp": ["tcp", "udp"], "icmp": ["icmp"]}
TARGETS = ("ACCEPT", "REJECT", "DROP")


def new_sid() -> str:
    return PREFIX + secrets.token_hex(4)


def _q(v) -> str:
    return "'" + str(v).replace("'", "'\\''") + "'"


def _ips(s: str) -> list[str] | None:
    out = []
    for x in (s or "").replace(",", " ").split():
        neg = x.startswith("!")
        try:
            n = ipaddress.ip_network(x.lstrip("!"), strict=False)
        except ValueError:
            return None
        v = str(n.network_address) if n.prefixlen == n.max_prefixlen else str(n)
        out.append(("!" if neg else "") + v)
    return out


def _ports(s: str) -> str | None:
    items = (s or "").replace(",", " ").split()
    for x in items:
        m = PORT_ITEM.match(x)
        if not m or not (1 <= int(m.group(1)) <= int(m.group(2) or m.group(1)) <= 65535):
            return None
    return " ".join(x.replace(":", "-") for x in items)


def proto_key(r: dict) -> str:
    p = sorted(r.get("proto") or [])
    for k, v in PROTOS.items():
        if sorted(v) == p:
            return k
    return "any" if not p or "all" in p else "tcpudp"


def check(fw: dict, form: dict, edit: dict | None) -> tuple[list[tuple], list[tuple], list[tuple], dict]:
    """(chyby, kriticke, varovani, data)."""
    errs, crit, warns = [], [], []
    zones = {z["name"]: z for z in fw.get("zones", [])}
    name = (form.get("name") or "").strip()
    src = form.get("src") or ""
    dest = form.get("dest") or ""               # "" = router (input), "*" = kamkoli, jinak zona
    pk = form.get("proto") or "any"
    target = (form.get("target") or "").upper()
    src_ip = _ips(form.get("src_ip") or "")
    dest_ip = _ips(form.get("dest_ip") or "")
    macs = [m.lower() for m in (form.get("src_mac") or "").replace(",", " ").split()]
    port = _ports(form.get("dest_port") or "")
    if not NAME_RE.match(name):
        errs.append(("rl.err_name", {}))
    if src not in zones and src != "*":
        errs.append(("rl.err_zone", {"v": src}))
    if dest not in zones and dest not in ("", "*"):
        errs.append(("rl.err_zone", {"v": dest}))
    if pk not in PROTOS or target not in TARGETS:
        errs.append(("msg.bad_request", {}))
    if src_ip is None:
        errs.append(("rl.err_ip", {"v": form.get("src_ip") or ""}))
    if dest_ip is None:
        errs.append(("rl.err_ip", {"v": form.get("dest_ip") or ""}))
    if any(not MAC_RE.match(m) for m in macs):
        errs.append(("rl.err_mac", {"v": form.get("src_mac") or ""}))
    if port is None:
        errs.append(("rl.err_port", {"v": form.get("dest_port") or ""}))
    elif port and pk not in ("tcp", "udp", "tcpudp"):
        errs.append(("rl.err_port_proto", {}))
    fam = ""
    allips = (src_ip or []) + (dest_ip or [])
    if allips:
        vers = {ipaddress.ip_network(x.lstrip("!"), strict=False).version for x in allips}
        if len(vers) > 1:
            errs.append(("rl.err_family", {}))
        fam = "ipv4" if vers == {4} else "ipv6"
    data = {"name": name, "src": src, "dest": dest, "proto": PROTOS.get(pk, []), "proto_key": pk,
            "dest_port": port or "", "src_ip": src_ip or [], "dest_ip": dest_ip or [], "src_mac": macs,
            "target": target, "family": fam, "enabled": True if not edit else edit["enabled"]}
    if errs:
        return errs, crit, warns, data
    wan = set(fw.get("wan_zones") or []) | ({fw["wan_zone"]} if fw.get("wan_zone") else set())
    pos_src = not any(not x.startswith("!") for x in data["src_ip"]) and not macs
    # otevreni z internetu
    if target == "ACCEPT" and (src in wan or src == "*"):
        if dest == "":
            risky = sorted({f"{p} ({RISKY[p]})" for p in RISKY if not port or port_in(p, port)})
            crit.append(("rl.c_wan_router", {"zone": src, "what": port or "*", "risky": ", ".join(risky[:6]) or "–"}))
        elif not data["dest_ip"]:
            crit.append(("rl.c_wan_zone", {"zone": src, "dest": dest or "*"}))
        elif pos_src:
            warns.append(("rl.w_wan_ip", {"zone": src, "ip": ", ".join(data["dest_ip"])}))
    # ochrana proti odriznuti (pravidlo, jak bude po zmene, a puvodni pri uprave)
    rr = {**data, "dest_port": port or "", "src_ip": data["src_ip"], "src_mac": macs}
    crit += lockout_rule(fw, rr, data["enabled"]) if data["enabled"] else []
    if edit:
        crit += [x for x in lockout_rule(fw, edit, False) if x not in crit] if edit["target"] == "ACCEPT" else []
    # bez ucinku: zona uz to povoluje / zakazuje
    if src in zones and pos_src and not port and pk == "any":
        z = zones[src]
        fwd_ok = dest in zones and any(f["src"] == src and f["dest"] == dest and f["enabled"] for f in fw.get("forwards", []))
        pol = (z["input"] == "ACCEPT") if dest == "" else (fwd_ok or (dest == src and z["forward"] == "ACCEPT"))
        if dest != "*" and not data["dest_ip"] and pol == (target == "ACCEPT"):
            warns.append(("rl.w_noop_allow" if target == "ACCEPT" else "rl.w_noop_block", {"src": src, "dest": dest or "router"}))
    return errs, crit, warns, data


def _nets(xs: list[str]) -> list | None:
    try:
        return [ipaddress.ip_network(x, strict=False) for x in xs if not x.startswith("!")]
    except ValueError:
        return None


def covers(a: dict, b: dict) -> bool:
    """Sedi pravidlo `a` na vsechno, na co sedi `b`? (zjednodusene: zona, cil, protokol, porty, IP; MAC a cilova IP
    u `a` = neumime posoudit -> ne)."""
    if a.get("src_mac") or a.get("dest_ip") or any(x.startswith("!") for x in a.get("src_ip") or []):
        return False
    if a["src"] not in (b["src"], "*"):
        return False
    if (a.get("dest") or "") != (b.get("dest") or "") and a.get("dest") != "*":
        return False
    pa = set(a.get("proto") or []) - {"all"}
    pb = set(b.get("proto") or []) - {"all"}
    if pa and (not pb or not pb <= pa):
        return False
    if a.get("dest_port"):
        if not b.get("dest_port"):
            return False
        if not all(port_in(x, a["dest_port"]) and port_in(y, a["dest_port"]) for x, y in ports_of(b["dest_port"])):
            return False
    if a.get("src_ip"):
        na, nb = _nets(a["src_ip"]), _nets(b.get("src_ip") or [])
        if na is None or not nb or not all(any(m.version == n.version and m.subnet_of(n) for n in na) for m in nb):
            return False
    return True


def _timed(r: dict) -> bool:
    return bool(r.get("time")) or any(k.startswith(("start_", "stop_", "weekdays", "monthdays")) for k in r.get("extra") or [])


def order_issues(fw: dict, d: dict, pos: float, own_id: str = "") -> list[tuple]:
    """Novy / upraveny pravidlo na pozici `pos`: zastinene drivejsim (nikdy nezabere), nebo samo zastini pozdejsi
    pravidlo s opacnou akci (napr. vyjimka „povolit jednu IP“ pod „zakazat celou sit“)."""
    out = []
    for r in fw.get("rules", []):
        if r["id"] == own_id or not r["enabled"] or _timed(r):
            continue
        if (r["target"] == "ACCEPT") == (d["target"] == "ACCEPT"):
            continue
        if r["pos"] < pos and covers(r, d):
            out.append(("rl.w_shadow", {"name": r["name"]}))
        elif r["pos"] > pos and covers(d, r):
            out.append(("rl.w_shadows", {"name": r["name"]}))
    return out[:3]


def set_cmds(sid: str, d: dict, new: bool) -> list[str]:
    p = f"firewall.{sid}"
    c = [f"uci set {p}=rule"] if new else []
    c += [f"uci set {p}.name={_q(d['name'])}", f"uci set {p}.src={_q(d['src'])}", f"uci set {p}.target={_q(d['target'])}"]
    for opt in ("dest", "dest_port", "family"):
        if d[opt]:
            c.append(f"uci set {p}.{opt}={_q(d[opt])}")
        elif not new:
            c.append(f"{{ uci -q delete {p}.{opt} || true; }}")
    for opt in ("proto", "src_ip", "dest_ip", "src_mac"):
        if not new:
            c.append(f"{{ uci -q delete {p}.{opt} || true; }}")
        vals = d[opt] if opt != "proto" else (d["proto"] or ["all"])
        c += [f"uci add_list {p}.{opt}={_q(v)}" for v in vals]
    return c


def reorder_cmd(sid: str, pos: int) -> str:
    return f"uci reorder firewall.{sid}={int(pos)}"


def neighbour(fw: dict, rid: str, direction: str) -> dict | None:
    rules = fw.get("rules", [])
    idx = next((i for i, r in enumerate(rules) if r["id"] == rid), None)
    if idx is None:
        return None
    j = idx - 1 if direction == "up" else idx + 1
    return rules[j] if 0 <= j < len(rules) else None
