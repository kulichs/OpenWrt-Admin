"""DNS na routeru (dnsmasq): mistni jmena (dhcp.@domain), aliasy CNAME (dhcp.@cname), test prekladu.

Data cte dhcp.READ_CMD (stejny soubor /etc/config/dhcp), zapis jde stejnou cestou jako DHCP
(nahled -> Pouzit -> uci commit dhcp + dnsmasq reload, pri chybe revert, zaloha pred prvni zmenou dne).
Nadrazene DNS (network.wan.dns / resolv.conf.auto) se jen ukazuje – zmena by restartovala WAN.
"""
from __future__ import annotations

import ipaddress
import re
import shlex

LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
DNSNAME_RE = re.compile(rf"^(?=.{{1,253}}$){LABEL}(\.{LABEL})*$")


def q(v: str) -> str:
    return shlex.quote(str(v))


def norm_name(s: str) -> str | None:
    s = (s or "").strip().rstrip(".")
    return s if DNSNAME_RE.match(s) else None


def norm_addr(s: str) -> str | None:
    try:
        return str(ipaddress.ip_address((s or "").strip()))
    except ValueError:
        return None


def local_names(model: dict) -> set[str]:
    """Jmena, ktera dnsmasq zna sam: mistni jmena, staticke zapujcky (i s mistni domenou), aktualni zapujcky."""
    dom = (model.get("dnsmasq") or {}).get("domain") or ""
    names = {d["name"].lower() for d in model.get("domains", [])}
    for h in model.get("hosts", []):
        if h["name"]:
            names.add(h["name"].lower())
    for l in model.get("leases", []):
        if l["hostname"]:
            names.add(l["hostname"].lower())
    if dom:
        names |= {f"{n}.{dom}" for n in list(names) if "." not in n}
    names |= {c["cname"].lower() for c in model.get("cnames", [])}
    return names


def check_domain(model: dict, name: str, ip: str, dom_id: str | None, t) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    if not norm_name(name):
        errs.append(t("dns.err_name"))
    if not norm_addr(ip):
        errs.append(t("dns.err_ip"))
    if errs:
        return errs, warns
    for d in model.get("domains", []):
        if d["id"] != dom_id and d["name"].lower() == name.lower():
            if d["ip"] == ip:
                errs.append(t("dns.err_dup", name=name, ip=ip))
            else:
                warns.append(t("dns.warn_multi", name=name, ip=d["ip"]))
    for c in model.get("cnames", []):
        if c["cname"].lower() == name.lower():
            errs.append(t("dns.err_is_cname", name=name))
    a = ipaddress.ip_address(ip)
    nets = model.get("_nets", {})
    if a.version == 4 and a.is_private and not any(a in v["net"] for v in nets.values()):
        warns.append(t("dns.warn_ip_net", ip=ip))
    dm = model.get("dnsmasq") or {}
    dom = dm.get("domain") or ""
    if "." not in name and dom:
        warns.append(t("dns.info_bare", name=name, dom=dom))
    return errs, warns


def check_cname(model: dict, cname: str, target: str, cid: str | None, t) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    if not norm_name(cname):
        errs.append(t("dns.err_name"))
    if not norm_name(target):
        errs.append(t("dns.err_target"))
    if errs:
        return errs, warns
    if cname.lower() == target.lower():
        errs.append(t("dns.err_loop"))
    for c in model.get("cnames", []):
        if c["id"] != cid and c["cname"].lower() == cname.lower():
            errs.append(t("dns.err_cname_dup", name=cname, target=c["target"]))
    if any(d["name"].lower() == cname.lower() for d in model.get("domains", [])):
        errs.append(t("dns.err_cname_is_name", name=cname))
    if target.lower() not in local_names(model):
        warns.append(t("dns.warn_target", target=target))
    return errs, warns


def domain_add_cmds(name: str, ip: str) -> list[str]:
    return ["s=$(uci add dhcp domain)", f'uci set "dhcp.$s.name"={q(name)}', f'uci set "dhcp.$s.ip"={q(ip)}']


def domain_edit_cmds(d: dict, name: str, ip: str) -> list[str]:
    cmds = []
    if name != d["name"]:
        cmds.append(f"uci set dhcp.{d['id']}.name={q(name)}")
    if ip != d["ip"]:
        cmds.append(f"uci set dhcp.{d['id']}.ip={q(ip)}")
    return cmds


def cname_add_cmds(cname: str, target: str) -> list[str]:
    return ["s=$(uci add dhcp cname)", f'uci set "dhcp.$s.cname"={q(cname)}', f'uci set "dhcp.$s.target"={q(target)}']


def cname_edit_cmds(c: dict, cname: str, target: str) -> list[str]:
    cmds = []
    if cname != c["cname"]:
        cmds.append(f"uci set dhcp.{c['id']}.cname={q(cname)}")
    if target != c["target"]:
        cmds.append(f"uci set dhcp.{c['id']}.target={q(target)}")
    return cmds


def del_cmds(sid: str) -> list[str]:
    return [f"uci delete dhcp.{sid}"]


def lookup_cmd(name: str) -> str:
    """Preklad pres dnsmasq na routeru (stejne jako se ptaji klienti)."""
    return f"nslookup {q(name)} 127.0.0.1 2>&1 | head -40"


def parse_lookup(out: str) -> dict:
    """BusyBox/bind nslookup -> {addrs, cname, nx}."""
    addrs, cname = [], ""
    seen_name = False
    for line in (out or "").splitlines():
        s = line.strip()
        m = re.match(r"^(\S+)\s+canonical name\s*=\s*(\S+?)\.?$", s)
        if m:
            cname = m.group(2)
        if s.startswith("Name:"):
            seen_name = True
        m = re.match(r"^Address(?:\s*\d+)?:\s*(\S+)", s)
        if m and seen_name:
            a = m.group(1).split("#")[0]
            if a not in addrs:
                addrs.append(a)
    nx = bool(re.search(r"NXDOMAIN|can't find|not found", out or "", re.I)) and not addrs
    return {"addrs": addrs, "cname": cname, "nx": nx}


def demo_lookup(model: dict, name: str) -> str:
    n = name.lower().rstrip(".")
    dom = (model.get("dnsmasq") or {}).get("domain") or "lan"
    base = n[: -len(dom) - 1] if n.endswith("." + dom) else n
    cn = next((c for c in model.get("cnames", []) if c["cname"].lower() == n), None)
    lines = ["Server:\t\t127.0.0.1", "Address:\t127.0.0.1:53", ""]
    target = cn["target"].lower() if cn else n
    if cn:
        lines.append(f"{n}\tcanonical name = {target}")
        base = target[: -len(dom) - 1] if target.endswith("." + dom) else target
    ips = [d["ip"] for d in model.get("domains", []) if d["name"].lower() in (target, base)]
    ips += [h["ip"] for h in model.get("hosts", []) if h["name"].lower() == base and h["ip"]]
    ips += [l["ip"] for l in model.get("leases", []) if l["hostname"].lower() == base]
    if not ips and n in ("doubleclick.net", "ads.example.com"):
        ips = ["0.0.0.0"]
    if not ips and "." in n and not n.endswith("." + dom):
        ips = ["93.184.215.14"]
    if not ips:
        return "\n".join(lines + [f"** server can't find {n}: NXDOMAIN"])
    lines.append("Name:\t" + target)
    lines += [f"Address: {ip}" for ip in dict.fromkeys(ips)]
    return "\n".join(lines)
