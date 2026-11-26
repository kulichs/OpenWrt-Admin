"""Uprava mapy zon zaskrtavanim: vstup na router (input), pruchod mezi zonami (forwarding), pruchod uvnitr zony.

Zmeny z matice prijdou jako seznam "input|zona|1", "fwd|odkud|kam|0", "zfwd|zona|1" (1 = povolit). Vsechny se ulozi
najednou jednou pojistkou. Pruchod mezi zonami = sekce `forwarding` (nova dostane jmeno owa_zf_<id>, odebrani
smaze vsechny zapnute sekce pro tu dvojici; vypnutou sekci pro dvojici pri povoleni jen zapne). Politika input /
forward se nastavi na ACCEPT, nebo REJECT.
"""
from __future__ import annotations

import re
import secrets

from .firewall import port_in
from .portfwd import mgmt

PREFIX = "owa_zf_"
SID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


def parse_changes(values: list[str], fw: dict) -> list[dict] | None:
    """Seznam zmen z formulare; None pri neplatnem vstupu. Duplicitni / neucinne zmeny se vynechaji."""
    zones = {z["name"]: z for z in fw.get("zones", [])}
    fw_pairs = {(f["src"], f["dest"]) for f in fw.get("forwards", []) if f["enabled"]}
    out, seen = [], set()
    for v in values:
        p = str(v).split("|")
        if len(p) == 3 and p[0] in ("input", "zfwd") and p[1] in zones and p[2] in ("0", "1"):
            key = (p[0], p[1])
            on = p[2] == "1"
            cur = zones[p[1]]["input" if p[0] == "input" else "forward"] == "ACCEPT"
            ch = {"kind": p[0], "zone": p[1], "on": on}
        elif len(p) == 4 and p[0] == "fwd" and p[1] in zones and p[2] in zones and p[1] != p[2] and p[3] in ("0", "1"):
            key = ("fwd", p[1], p[2])
            on = p[3] == "1"
            cur = (p[1], p[2]) in fw_pairs
            ch = {"kind": "fwd", "src": p[1], "dest": p[2], "on": on}
        else:
            return None
        if key in seen or on == cur:
            continue
        seen.add(key)
        out.append(ch)
    return out


def cmds_for(changes: list[dict], fw: dict) -> list[str]:
    zones = {z["name"]: z for z in fw["zones"]}
    cmds = []
    for c in changes:
        if c["kind"] in ("input", "zfwd"):
            sid = zones[c["zone"]]["id"]
            if not SID_RE.match(sid):
                raise ValueError(sid)
            opt = "input" if c["kind"] == "input" else "forward"
            cmds.append(f"uci set firewall.{sid}.{opt}={'ACCEPT' if c['on'] else 'REJECT'}")
            continue
        pairs = [f for f in fw["forwards"] if f["src"] == c["src"] and f["dest"] == c["dest"]]
        if c["on"]:
            off = next((f for f in pairs if not f["enabled"] and SID_RE.match(f["id"])), None)
            if off:
                cmds.append(f"uci -q delete firewall.{off['id']}.enabled")
            else:
                sid = PREFIX + secrets.token_hex(4)
                cmds += [f"uci set firewall.{sid}=forwarding", f"uci set firewall.{sid}.src='{c['src']}'",
                         f"uci set firewall.{sid}.dest='{c['dest']}'"]
        else:
            for f in pairs:
                if f["enabled"] and SID_RE.match(f["id"]):
                    cmds.append(f"uci delete firewall.{f['id']}")
    return cmds


def _accepts(fw: dict, zone: str, port: int, proto: str) -> bool:
    """Povoluje nejake zapnute pravidlo ACCEPT ze zony na router dany port?"""
    for r in fw.get("rules", []):
        if not r["enabled"] or r["dest"] or r["target"] != "ACCEPT" or r["src"] not in (zone, "*"):
            continue
        if r["src_ip"] or r["src_mac"]:
            continue
        if r["proto"] and proto not in r["proto"] and "all" not in r["proto"]:
            continue
        if not r["dest_port"] or port_in(port, r["dest_port"]):
            return True
    return False


def risks(changes: list[dict], fw: dict) -> tuple[list[tuple], list[tuple], list[str]]:
    """(kriticke, varovani, popisy) – klice pro preklad s argumenty; popisy = (klic, args) pro radek zmeny."""
    zones = {z["name"]: z for z in fw["zones"]}
    wan = set(fw.get("wan_zones") or []) | ({fw["wan_zone"]} if fw.get("wan_zone") else set())
    wan_nets = {"wan", "wan6"}
    mg = mgmt(fw)
    crit, warn, desc = [], [], []
    for c in changes:
        if c["kind"] == "fwd":
            desc.append(("zm.do_fwd_on" if c["on"] else "zm.do_fwd_off", {"src": c["src"], "dest": c["dest"]}))
            if c["on"] and c["src"] in wan:
                crit.append(("zm.c_wan_fwd", {"src": c["src"], "dest": c["dest"]}))
            elif c["on"] and c["dest"] not in wan:
                warn.append(("zm.w_fwd", {"src": c["src"], "dest": c["dest"]}))
            if not c["on"] and c["dest"] in wan:
                warn.append(("zm.w_no_inet", {"src": c["src"]}))
            n = len([r for r in fw.get("rules", []) if r["enabled"] and r["src"] == c["src"] and r["dest"] == c["dest"]])
            if n:
                warn.append(("zm.w_rules", {"src": c["src"], "dest": c["dest"], "n": n}))
            for zn in (c["src"], c["dest"]):
                if not zones[zn]["devices"] and fw.get("fwver") == "fw4" and fw.get("matrix"):
                    warn.append(("zm.w_empty", {"zone": zn}))
            continue
        z = zones[c["zone"]]
        if c["kind"] == "input":
            desc.append(("zm.do_in_on" if c["on"] else "zm.do_in_off", {"zone": c["zone"]}))
            if c["on"] and c["zone"] in wan:
                crit.append(("zm.c_wan_input", {"zone": c["zone"]}))
            if not c["on"]:
                if mg["zone"] == c["zone"] and mg["port"] and not _accepts(fw, c["zone"], mg["port"], "tcp"):
                    crit.append(("zm.c_lock", {"zone": c["zone"], "port": mg["port"]}))
                mixed = any(n not in wan_nets for n in z["networks"])
                if c["zone"] not in wan or mixed:
                    miss = [lbl for port, proto, lbl in ((67, "udp", "DHCP"), (53, "udp", "DNS"))
                            if not _accepts(fw, c["zone"], port, proto)]
                    if miss:
                        warn.append(("zm.w_dhcp_dns", {"zone": c["zone"], "what": " + ".join(miss)}))
                n = len([r for r in fw.get("rules", []) if r["enabled"] and r["src"] == c["zone"] and not r["dest"]])
                if n:
                    warn.append(("zm.w_in_rules", {"zone": c["zone"], "n": n}))
        else:
            desc.append(("zm.do_z_on" if c["on"] else "zm.do_z_off", {"zone": c["zone"]}))
            if c["on"] and c["zone"] in wan:
                crit.append(("zm.c_wan_zfwd", {"zone": c["zone"]}))
        other = [n for n in z["networks"] if n not in wan_nets]
        if c["zone"] in wan and other and any(n in wan_nets for n in z["networks"]):
            warn.append(("zm.w_mixed", {"zone": c["zone"], "nets": ", ".join(other)}))
        if not z["devices"] and fw.get("matrix"):
            warn.append(("zm.w_empty", {"zone": c["zone"]}))
    # bez duplicit, poradi zachovat
    def uniq(xs):
        seen, out = set(), []
        for k, a in xs:
            h = (k, tuple(sorted(a.items())))
            if h not in seen:
                seen.add(h)
                out.append((k, a))
        return out
    return uniq(crit), uniq(warn), desc


def demo_text(text: str, ops: list[dict]) -> str:
    """Demo: promitnout ulozene zmeny matice do demo textu uci."""
    for o in ops:
        if o["op"] == "set":
            pat = re.compile(rf"^firewall\.{re.escape(o['sid'])}\.{o['opt']}=.*$", re.M)
            line = f"firewall.{o['sid']}.{o['opt']}='{o['val']}'"
            text = pat.sub(line, text) if pat.search(text) else text.replace("@@dispatch", line + "\n@@dispatch", 1)
        elif o["op"] == "del":
            text = "\n".join(l for l in text.splitlines() if not re.match(rf"firewall\.{re.escape(o['sid'])}[.=]", l)) + "\n"
        elif o["op"] == "add":
            text = text.replace("@@dispatch", f"firewall.{o['sid']}=forwarding\nfirewall.{o['sid']}.src='{o['src']}'\n"
                                f"firewall.{o['sid']}.dest='{o['dest']}'\n@@dispatch", 1)
        elif o["op"] == "unset":
            text = "\n".join(l for l in text.splitlines() if l != f"firewall.{o['sid']}.{o['opt']}='0'") + "\n"
    return text


def demo_ops(cmds: list[str]) -> list[dict]:
    """Demo: uci prikazy -> operace pro demo_text."""
    ops, adds = [], {}
    for c in cmds:
        m = re.match(r"uci set firewall\.(\w+)\.(input|forward)=(\w+)$", c)
        if m:
            ops.append({"op": "set", "sid": m.group(1), "opt": m.group(2), "val": m.group(3)})
            continue
        m = re.match(r"uci set firewall\.(\w+)\.(src|dest)='([^']*)'$", c)
        if m:
            adds.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
            continue
        m = re.match(r"uci delete firewall\.(\w+)$", c)
        if m:
            ops.append({"op": "del", "sid": m.group(1)})
            continue
        m = re.match(r"uci -q delete firewall\.(\w+)\.enabled$", c)
        if m:
            ops.append({"op": "unset", "sid": m.group(1), "opt": "enabled"})
    for sid, d in adds.items():
        ops.append({"op": "add", "sid": sid, "src": d.get("src", ""), "dest": d.get("dest", "")})
    return ops
