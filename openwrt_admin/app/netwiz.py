"""Pruvodce novou siti na routeru (VLAN + rozhrani + DHCP + zona firewallu) a smazani site s uklidem.

Vse jde jednim nahledem a jednou pojistkou (network, dhcp, firewall). Nove sekce maji jmena owa_*_<sit>, aby se
daly pozdeji najit a uklidit. Zpusob VLAN se bere z toho, jak ma router VLAN uz nastavene:
  - DSA s bridge-vlan:  nova sekce bridge-vlan (porty "<port>:t"), rozhrani na <most>.<vid>
  - swconfig:            nova switch_vlan ("<cpu>t <port>t"), most br-<sit> s <cpu-eth>.<vid>
  - VLAN na portu:       zarizeni 8021q <port>.<vid>, most br-<sit>
DSA bez bridge-vlan (VLAN filtrovani vypnute) se neupravuje – zapnuti by menilo celou LAN; jen navod.
"""
from __future__ import annotations

import ipaddress
import re

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,11}$")     # br-<jmeno> max 15 znaku
ZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,10}$")     # fw zona max 11 znaku (kompatibilita)
SID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
RESERVED = {"loopback", "lan", "wan", "wan6", "globals"}


def _q(v) -> str:
    return "'" + str(v).replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- moznosti

def trunks(raw: dict) -> list[dict]:
    """Kudy muze nova VLAN vest. id: dsa:<most>, sw, q:<port>."""
    out = []
    for br, vl in (raw.get("dsa") or {}).items():
        ports = raw["dsa_ports"].get(br) or sorted({p.split(":")[0] for ps in vl.values() for p in ps})
        out.append({"id": f"dsa:{br}", "kind": "dsa", "label": f"{br} (DSA bridge-vlan)", "ports": ports,
                    "default": [raw["dsa_up"].get(br)] if raw["dsa_up"].get(br) else []})
    if raw.get("sw_dev"):
        out.append({"id": "sw", "kind": "sw", "label": f"{raw['sw_dev']} (swconfig)", "ports": raw["sw_ports"],
                    "default": [raw["sw_up"]] if raw.get("sw_up") else []})
    for p in sorted(set(raw.get("plain_up") or []) | set(raw.get("phys") or [])):
        out.append({"id": f"q:{p}", "kind": "q", "label": p, "ports": [], "default": []})
    return out


def subnets(raw: dict) -> list[ipaddress.IPv4Network]:
    out = []
    for i in raw.get("ifaces", {}).values():
        if "/" in (i.get("ip") or ""):
            try:
                out.append(ipaddress.ip_interface(i["ip"]).network)
            except ValueError:
                pass
    return out


def suggest_subnet(raw: dict, vid: int | None = None) -> str:
    used = subnets(raw)
    cands = []
    if vid and 1 < vid < 255:
        cands.append(f"192.168.{vid}.0/24")
    cands += [f"192.168.{x}.0/24" for x in range(2, 255)]
    for c in cands:
        n = ipaddress.ip_network(c)
        if not any(n.overlaps(u) for u in used):
            return f"{n.network_address + 1}/{n.prefixlen}"
    return ""


def free_vid(raw: dict) -> int:
    used = set(raw.get("used_vids") or [])
    for v in (20, 50, 60, 70, 80, 90, *range(2, 4095)):
        if v not in used:
            return v
    return 0


# ---------------------------------------------------------------- nova sit

def check_add(raw: dict, fw: dict, dhcp_uci: dict, form: dict, mgmt_zone: str) -> tuple[list[tuple], list[tuple], dict]:
    errs, warns = [], []
    name = (form.get("name") or "").strip()
    try:
        vid = int(form.get("vid") or 0)
    except ValueError:
        vid = 0
    trunk_id = form.get("trunk") or ""
    ports = [p for p in form.get("ports") or [] if p]
    cidr = (form.get("cidr") or "").strip()
    zone_mode = form.get("zone_mode") or "new"
    zone_name = (form.get("zone_name") or name.lower()[:11]).strip()
    zone_existing = form.get("zone_existing") or ""
    inet = bool(form.get("inet"))
    dhcp_on = bool(form.get("dhcp"))
    try:
        start, limit = int(form.get("start") or 100), int(form.get("limit") or 150)
    except ValueError:
        start, limit = 0, 0
    if not NAME_RE.match(name) or name.lower() in RESERVED:
        errs.append(("nw.err_name", {"v": name}))
    elif name in raw["ifaces"] or name.lower() in {x.lower() for x in raw["ifaces"]}:
        errs.append(("nw.err_name_used", {"v": name}))
    if name in dhcp_uci:
        errs.append(("nw.err_dhcp_used", {"v": name}))
    if not 2 <= vid <= 4094:
        errs.append(("nw.err_vid", {"v": form.get("vid") or ""}))
    elif vid in (raw.get("used_vids") or []):
        errs.append(("nw.err_vid_used", {"v": vid}))
    tr = next((x for x in trunks(raw) if x["id"] == trunk_id), None)
    if not tr:
        errs.append(("nw.err_trunk", {}))
    elif tr["kind"] in ("dsa", "sw"):
        if not ports:
            errs.append(("nw.err_ports", {}))
        elif any(p not in tr["ports"] for p in ports):
            errs.append(("msg.bad_request", {}))
    try:
        iface = ipaddress.IPv4Interface(cidr)
        if not 16 <= iface.network.prefixlen <= 30 or iface.ip in (iface.network.network_address, iface.network.broadcast_address):
            raise ValueError
    except ValueError:
        iface = None
        errs.append(("nw.err_cidr", {"v": cidr}))
    if iface:
        for u in subnets(raw):
            if iface.network.overlaps(u):
                errs.append(("nw.err_overlap", {"v": str(iface.network), "u": str(u)}))
        if not iface.network.is_private:
            warns.append(("nw.w_public", {"v": str(iface.network)}))
        if dhcp_on:
            hosts = iface.network.num_addresses - 2
            if not (1 <= start and limit >= 1 and start + limit - 1 <= hosts):
                errs.append(("nw.err_pool", {"max": hosts}))
            elif start <= int(iface.ip) - int(iface.network.network_address) < start + limit:
                warns.append(("nw.w_pool_router", {"ip": str(iface.ip)}))
    zones = {z["name"]: z for z in fw.get("zones", [])}
    if zone_mode == "new":
        if not ZONE_RE.match(zone_name):
            errs.append(("nw.err_zone", {"v": zone_name}))
        elif zone_name in zones:
            errs.append(("nw.err_zone_used", {"v": zone_name}))
        if not inet:
            warns.append(("nw.w_no_inet", {}))
        elif not fw.get("wan_zone"):
            errs.append(("blk.err_nowan", {}))
    elif zone_mode == "existing":
        if zone_existing not in zones:
            errs.append(("nw.err_zone_pick", {}))
        else:
            z = zones[zone_existing]
            if z["input"] == "ACCEPT":
                warns.append(("nw.w_zone_trusted", {"zone": zone_existing}))
    else:
        warns.append(("nw.w_no_zone", {}))
    if fw.get("fwver") != "fw4":
        errs.append(("nw.err_fw4", {}))
    data = {"name": name, "vid": vid, "trunk": tr, "ports": ports, "cidr": str(iface) if iface else "",
            "ip": str(iface.ip) if iface else "", "mask": str(iface.netmask) if iface else "",
            "zone_mode": zone_mode, "zone_name": zone_name, "zone_existing": zone_existing,
            "zone_sid": zones[zone_existing]["id"] if zone_existing in zones else "", "inet": inet,
            "wan_zone": fw.get("wan_zone") or "", "dhcp": dhcp_on, "start": start, "limit": limit}
    return errs, warns, data


def add_cmds(raw: dict, d: dict) -> list[str]:
    n, v, tr = d["name"], d["vid"], d["trunk"]
    c = []
    if tr["kind"] == "dsa":
        br = tr["id"].split(":", 1)[1]
        s = f"network.owa_bv_{n}"
        c += [f"uci set {s}=bridge-vlan", f"uci set {s}.device={_q(br)}", f"uci set {s}.vlan='{v}'"]
        c += [f"uci add_list {s}.ports={_q(p + ':t')}" for p in d["ports"]]
        dev = f"{br}.{v}"
    elif tr["kind"] == "sw":
        s = f"network.owa_sv_{n}"
        ports = " ".join([f"{raw['cpu_port']}t", *[f"{p}t" for p in d["ports"]]])
        c += [f"uci set {s}=switch_vlan", f"uci set {s}.device={_q(raw['sw_dev'])}",
              f"uci set {s}.vlan='{raw['sw_idx'] + 1}'", f"uci set {s}.vid='{v}'", f"uci set {s}.ports={_q(ports)}"]
        l2 = f"{raw['cpu_eth'] or 'eth0'}.{v}"
        b = f"network.owa_br_{n}"
        c += [f"uci set {b}=device", f"uci set {b}.type='bridge'", f"uci set {b}.name='br-{n}'",
              f"uci add_list {b}.ports={_q(l2)}"]
        dev = f"br-{n}"
    else:
        base = tr["id"].split(":", 1)[1]
        q = f"network.owa_vd_{n}"
        c += [f"uci set {q}=device", f"uci set {q}.type='8021q'", f"uci set {q}.ifname={_q(base)}",
              f"uci set {q}.vid='{v}'", f"uci set {q}.name={_q(f'{base}.{v}')}"]
        b = f"network.owa_br_{n}"
        c += [f"uci set {b}=device", f"uci set {b}.type='bridge'", f"uci set {b}.name='br-{n}'",
              f"uci add_list {b}.ports={_q(f'{base}.{v}')}"]
        dev = f"br-{n}"
    i = f"network.{n}"
    c += [f"uci set {i}=interface", f"uci set {i}.proto='static'", f"uci set {i}.device={_q(dev)}",
          f"uci set {i}.ipaddr={_q(d['ip'])}", f"uci set {i}.netmask={_q(d['mask'])}"]
    if d["dhcp"]:
        h = f"dhcp.{n}"
        c += [f"uci set {h}=dhcp", f"uci set {h}.interface={_q(n)}", f"uci set {h}.start='{d['start']}'",
              f"uci set {h}.limit='{d['limit']}'", "uci set " + h + ".leasetime='12h'"]
    if d["zone_mode"] == "new":
        z, zn = f"firewall.owa_zn_{n}", d["zone_name"]
        c += [f"uci set {z}=zone", f"uci set {z}.name={_q(zn)}", f"uci add_list {z}.network={_q(n)}",
              f"uci set {z}.input='REJECT'", f"uci set {z}.output='ACCEPT'", f"uci set {z}.forward='REJECT'"]
        if d["inet"]:
            f = f"firewall.owa_zf_{n}"
            c += [f"uci set {f}=forwarding", f"uci set {f}.src={_q(zn)}", f"uci set {f}.dest={_q(d['wan_zone'])}"]
        r1, r2 = f"firewall.owa_r_{n}_dhcp", f"firewall.owa_r_{n}_dns"
        c += [f"uci set {r1}=rule", f"uci set {r1}.name={_q(f'Allow-{zn}-DHCP')}", f"uci set {r1}.src={_q(zn)}",
              f"uci set {r1}.proto='udp'", f"uci set {r1}.dest_port='67-68'", f"uci set {r1}.family='ipv4'",
              f"uci set {r1}.target='ACCEPT'",
              f"uci set {r2}=rule", f"uci set {r2}.name={_q(f'Allow-{zn}-DNS')}", f"uci set {r2}.src={_q(zn)}",
              f"uci add_list {r2}.proto='tcp'", f"uci add_list {r2}.proto='udp'", f"uci set {r2}.dest_port='53'",
              f"uci set {r2}.target='ACCEPT'"]
    elif d["zone_mode"] == "existing":
        c.append(f"uci add_list firewall.{d['zone_sid']}.network={_q(n)}")
    return c


def reload_cmds(dhcp: bool) -> list[str]:
    return ["/etc/init.d/network reload", *(["/etc/init.d/dnsmasq reload"] if dhcp else []), "/etc/init.d/firewall reload"]


# ---------------------------------------------------------------- smazani site

def check_del(raw: dict, fw: dict, name: str, mgmt_ip: str) -> list[tuple]:
    errs = []
    i = raw["ifaces"].get(name)
    if not i or not SID_RE.match(name):
        return [("msg.bad_request", {})]
    if name.lower() in RESERVED or name.startswith("wan"):
        errs.append(("nw.err_del_reserved", {"v": name}))
    if mgmt_ip and "/" in (i.get("ip") or ""):
        try:
            if ipaddress.ip_address(mgmt_ip) in ipaddress.ip_interface(i["ip"]).network:
                errs.append(("nw.err_del_mgmt", {"v": name}))
        except ValueError:
            pass
    if i["proto"] not in ("static", "none", "dhcp"):
        errs.append(("nw.err_del_proto", {"v": name, "p": i["proto"]}))
    return errs


def del_plan(raw: dict, fw: dict, dhcp_uci: dict, name: str) -> tuple[list[str], list[str]]:
    """(prikazy, popisy uklidu). Smaze jen to, co pouziva jen tahle sit."""
    i = raw["ifaces"][name]
    dev = i["device"]
    others = [x for k, x in raw["ifaces"].items() if k != name]
    other_devs = {x["device"] for x in others}
    cmds, what = [f"uci delete network.{name}"], [f"network.{name}"]
    # most br-<x> jen s VLAN portem teto site
    l2s = []
    if dev in raw["bridges"] and dev not in other_devs:
        ports = raw["bridges"][dev]
        other_ports = {p for b, ps in raw["bridges"].items() if b != dev for p in ps}
        sid = raw["bridge_sid"].get(dev, "")
        if SID_RE.match(sid) and dev not in raw.get("dsa", {}):
            cmds.append(f"uci delete network.{sid}")
            what.append(f"{dev}")
            l2s = [p for p in ports if p not in other_ports and p not in other_devs]
    elif dev not in other_devs:
        l2s = [dev]
    for l2 in l2s:
        m = re.match(r"^(.+)\.(\d+)$", l2)
        if not m:
            continue
        base, vid = m.group(1), int(m.group(2))
        x = raw["l2"].get(l2)
        if x and SID_RE.match(x["sid"]):
            cmds.append(f"uci delete network.{x['sid']}")
            what.append(l2)
        sm = raw["sw_meta"].get(vid) if raw.get("sw_dev") and base == raw.get("cpu_eth") else None
        if sm and SID_RE.match(sm["sid"]):
            cmds.append(f"uci delete network.{sm['sid']}")
            what.append(f"switch VLAN {vid}")
        bv = raw["dsa_sid"].get(f"{base}|{vid}")
        if bv and SID_RE.match(bv):
            cmds.append(f"uci delete network.{bv}")
            what.append(f"bridge-vlan {base} {vid}")
    for sid, o in dhcp_uci.items():
        iface = o.get("interface") if isinstance(o.get("interface"), str) else ""
        if o.get("_type") == "dhcp" and (iface or sid) == name and SID_RE.match(sid):
            cmds.append(f"uci delete dhcp.{sid}")
            what.append(f"dhcp.{sid}")
    for z in fw.get("zones", []):
        if name not in z["networks"] or not SID_RE.match(z["id"]):
            continue
        rest = [n for n in z["networks"] if n != name]
        if rest or z.get("has_devices_opt"):
            cmds.append(f"uci del_list firewall.{z['id']}.network={_q(name)}")
            what.append(f"firewall {z['name']}: −{name}")
            continue
        cmds.append(f"uci delete firewall.{z['id']}")
        what.append(f"firewall {z['name']}")
        for f in fw.get("forwards", []):
            if z["name"] in (f["src"], f["dest"]) and SID_RE.match(f["id"]):
                cmds.append(f"uci delete firewall.{f['id']}")
        for r in fw.get("rules", []) + fw.get("redirects", []):
            if z["name"] in (r["src"], r["dest"]) and SID_RE.match(r["id"]):
                cmds.append(f"uci delete firewall.{r['id']}")
                what.append(f"firewall „{r['name']}“")
    return cmds, what


# ---------------------------------------------------------------- demo: jednoduchy uci nad textem `uci show`

def uci_sim(text: str, cmds: list[str], pkg: str) -> str:
    """Demo: provede uci set/add_list/del_list/delete pro balik `pkg` nad textem `uci show`."""
    lines = text.splitlines()

    def find(key):
        return next((k for k, l in enumerate(lines) if l.split("=", 1)[0] == key), None)

    def last_pkg():
        idx = [k for k, l in enumerate(lines) if l.startswith(pkg + ".")]
        return (idx[-1] + 1) if idx else len(lines)

    for c in cmds:
        c = c.strip().strip("{}").replace("|| true;", "").replace("|| true", "").strip().rstrip(";").strip()
        m = re.match(r"^uci (?:-q )?(set|add_list|del_list|delete) (\S+?)(?:=(.*))?$", c)
        if not m or not m.group(2).startswith(pkg + "."):
            continue
        op, key, val = m.group(1), m.group(2), (m.group(3) or "")
        val = val.strip()
        if val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("'\\''", "'")
        k = find(key)
        if op == "set":
            line = f"{key}={val}" if key.count(".") == 1 else f"{key}='{val}'"
            if k is None:
                lines.insert(last_pkg(), line)
            else:
                lines[k] = line
        elif op == "add_list":
            if k is None:
                lines.insert(last_pkg(), f"{key}='{val}'")
            else:
                lines[k] = lines[k] + f" '{val}'"
        elif op == "del_list" and k is not None:
            vals = re.findall(r"'([^']*)'", lines[k].split("=", 1)[1])
            vals = [x for x in vals if x != val]
            if vals:
                lines[k] = key + "=" + " ".join(f"'{x}'" for x in vals)
            else:
                del lines[k]
        elif op == "delete":
            lines = [l for l in lines if l.split("=", 1)[0] != key and not l.startswith(key + ".")]
    return "\n".join(lines) + "\n"
