"""Mapa site: uzly (internet, router, AP, SSID, kabelova zarizeni) a spojnice (WAN, LAN/trunk s VLANy, Wi-Fi).

Fyzicke zapojeni (switche, porty) z ubus nevidime. Spojnice router -> AP znamena "AP je ve stejne
siti a nese tyto site/VLANy", ne konkretni kabel. VLAN ID se bere z nazvu zarizeni (eth0.30,
br-lan.10) nebo z clenu bridge (br-host -> eth0.30), netagovana sit = LAN.
"""
from __future__ import annotations

import ipaddress
import time
from urllib.parse import quote, urlencode

from .collector import APState, is_l2_iface
from .wired import speed_label

PALETTE = ["#03a9f4", "#8e44ad", "#e67e22", "#16a085", "#c0392b", "#2c7be5", "#b7950b", "#7f8c8d"]

COL_X = {"internet": 20, "router": 225, "ap": 530, "ssid": 880, "client": 1190}
NODE_W = {"internet": 170, "router": 240, "ap": 280, "ssid": 250, "wired": 280, "client": 250}
NODE_H = 52
ROW = 66
CLIENT_H = 28
CROW = 34
TOP = 20


def _chips(node: dict, chips: list[dict]) -> list[dict]:
    """Stitky VLAN vpravo dole v uzlu; kdyz se nevejdou vedle IP, zuzi se na samotne cislo."""
    if not chips:
        return []
    room = node["w"] - 120  # vlevo je IP adresa
    w, gap = 48, 4
    if len(chips) * (w + gap) > room or any(len(c["label"]) > 7 for c in chips) and len(chips) > 2:
        w = max(24, min(34, room // len(chips) - gap))
        chips = [{**c, "label": c["label"].replace("VLAN ", "")} for c in chips]
    x0 = node["x"] + node["w"] - 10 - len(chips) * (w + gap) + gap
    return [{**c, "x": x0 + k * (w + gap), "y": node["y"] + 28, "w": w} for k, c in enumerate(chips)]


def _net_key(vlan: int | None) -> str:
    return "lan" if vlan is None else f"vlan{vlan}"


def _eff_vlans(dev: APState) -> dict[str, int | None]:
    """Rozhrani -> VLAN pro mapu. Rozhrani s IP zarizeni nebo pojmenovane 'lan' = management LAN (netagovana),
    i kdyz je interne eth0.1 / br-lan.1 (swconfig/DSA, na kabelu bez tagu)."""
    out = {}
    host = (dev.host or "").split(":")[0]
    for i in dev.interfaces:
        if not is_l2_iface(i):  # WAN, wg0 a jine tunely nejsou LAN ani VLAN
            continue
        mgmt = i["name"] == "lan" or any(a.split("/")[0] == host for a in (i.get("ipv4") or "").split(", ") if a)
        out[i["name"]] = None if mgmt else i.get("vlan")
    return out


def net_resolver(router: APState | None):
    """-> funkce ip -> {"iface": jmeno, "vlan": VLAN ID nebo None (LAN)} podle podsiti rozhrani routeru;
    None, kdyz IP nepatri do zadne LAN/VLAN routeru."""
    nets = []
    if router:
        vl = _eff_vlans(router)
        for i in router.interfaces:
            if i["name"] not in vl:
                continue
            for a in (i.get("ipv4") or "").split(", "):
                try:
                    nets.append((ipaddress.ip_interface(a).network, i["name"], vl[i["name"]]))
                except ValueError:
                    pass
    nets.sort(key=lambda n: -n[0].prefixlen)

    def resolve(ip: str):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for net, name, vlan in nets:
            if addr in net:
                return {"iface": name, "vlan": vlan}
        return None
    return resolve


def net_label(vlan, t) -> str:
    return t("map.lan") if vlan is None else f"VLAN {vlan}"


def _ago(ts: float, t) -> str:
    s = int(time.time() - ts)
    if s < 7200:
        return t("fmt.ago_m", m=max(1, s // 60))
    if s < 172800:
        return t("fmt.ago_h", h=s // 3600)
    return t("fmt.ago_d", d=s // 86400)


def _client_node(c: dict, x: float, y: float, color: str, wired: bool, t) -> dict:
    name = c.get("hostname") or c.get("mac", "")
    if wired:
        extra = c.get("ip", "")
    else:
        extra = f"{c['signal']} dBm" if c.get("signal") is not None else ""
    tip = " · ".join(v for v in [name, c.get("ip", ""), c.get("mac", ""), (c.get("band") or ""),
                                 (c.get("standard") or ""), extra if not wired else ""] if v)
    sig = c.get("signal")
    cls = "" if wired or sig is None else ("good" if sig >= -60 else "ok" if sig >= -72 else "bad")
    return {"id": "client:" + c.get("mac", ""), "kind": "client", "x": x, "y": y, "w": NODE_W["client"], "h": CLIENT_H,
            "title": name, "sub": "", "extra": extra, "sig": cls, "online": True, "color": color, "tip": tip,
            "href": ""}


INDENT = 58


def _tree_order(aps: list[APState], parent: dict[str, str]) -> list[tuple[APState, int]]:
    """AP v poradi stromu (podrizene hned pod nadrazenym) s hloubkou zanoreni."""
    names = {a.name for a in aps}
    kids: dict[str, list[APState]] = {}
    roots = []
    for a in aps:
        p = parent.get(a.name)
        (kids.setdefault(p, []) if p in names else roots).append(a)
    out, seen = [], set()

    def walk(a, d):
        if a.name in seen:
            return
        seen.add(a.name)
        out.append((a, d))
        for k in kids.get(a.name, []):
            walk(k, d + 1)
    for r in roots:
        walk(r, 0)
    for a in aps:  # cykly / zbytek
        walk(a, 0)
    return out


def build(router: APState | None, aps: list[APState], wired: list[dict], t, clients: list[dict] | None = None,
          show_clients: bool = True, show_empty: bool = False,
          ap_wired: dict[str, list[dict]] | None = None, parent: dict[str, str] | None = None,
          speeds: dict[str, int] | None = None) -> dict:
    """wired: kabelova zarizeni u routeru (neumistena na zadny AP); clients: vsichni Wi-Fi klienti;
    ap_wired: {ap: [kabelova zarizeni na portech AP]}; parent: {ap: AP, pres ktery je zapojeny}."""
    clients = clients or []
    ap_wired = ap_wired or {}
    parent = parent or {}
    speeds = speeds or {}
    # ---- site (podle VLAN ID), nazvy primarne z routeru
    nets: dict[str, dict] = {}

    def net(vlan: int | None, name: str = "") -> dict:
        k = _net_key(vlan)
        if k not in nets:
            nets[k] = {"key": k, "vlan": vlan, "names": []}
        if name and name not in nets[k]["names"]:
            nets[k]["names"].append(name)
        return nets[k]

    net(None, "lan")
    router_vl = _eff_vlans(router) if router else {}
    for name, v in router_vl.items():
        net(v, name)
    by_name = {n.lower(): v for n, v in router_vl.items() if v is not None}
    ap_ifvlan: dict[str, dict[str, int | None]] = {}
    for ap in aps:
        m = _eff_vlans(ap)
        for name, v in list(m.items()):
            if v is None and name != "lan" and name.lower() in by_name and not any(
                    a for a in (next((i for i in ap.interfaces if i["name"] == name), {}).get("ipv4") or "").split(", ")
                    if a.split("/")[0] == ap.host):
                m[name] = by_name[name.lower()]  # VLAN se nepodarilo zjistit -> podle stejneho jmena site na routeru
            net(m[name], name)
        ap_ifvlan[ap.name] = m
    for ap in aps:  # i AP bez dat o rozhranich: sit z wireless podle jmena na routeru
        for s_ in ap.ssids:
            first = (s_.get("network") or "").split(",")[0].strip()
            if first and first not in ap_ifvlan[ap.name] and first.lower() in by_name:
                ap_ifvlan[ap.name][first] = by_name[first.lower()]

    order = sorted(nets.values(), key=lambda n: (n["vlan"] is not None, n["vlan"] or 0))
    for idx, n in enumerate(order):
        n["color"] = PALETTE[idx % len(PALETTE)]
        n["label"] = (t("map.lan") if n["vlan"] is None else f"VLAN {n['vlan']}")
        n["names_str"] = ", ".join(n["names"])

    def net_of_ap_network(ap: APState, network: str) -> dict:
        # SSID muze byt ve vice sitich ("lan, iot") -> prvni
        first = (network or "").split(",")[0].strip()
        vlan = ap_ifvlan.get(ap.name, {}).get(first)
        return nets[_net_key(vlan)] if _net_key(vlan) in nets else net(vlan, first)

    # ---- rozlozeni: SSID blok = max(radek SSID, klienti pod sebou), AP blok = soucet SSID bloku
    by_if: dict[tuple, list] = {}
    for c in clients:
        by_if.setdefault((c["ap"], c.get("ifname")), []).append(c)
    for v in by_if.values():
        v.sort(key=lambda c: ((c.get("hostname") or "~").lower(), c.get("mac", "")))

    nodes, edges = [], []
    y = TOP
    ap_nodes = []
    hidden = 0
    for ap, depth in _tree_order(aps, parent):
        ssids = ap.ssids if ap.online else []
        cab = ap_wired.get(ap.name, []) if ap.online else []
        if not show_empty:
            hidden += sum(1 for s in ssids if not s.get("clients"))
            ssids = [s for s in ssids if s.get("clients")]
        block_top = y
        children = []
        if cab:
            cl = cab if show_clients else []
            h = max(ROW, len(cl) * CROW + (ROW - CROW))
            children.append(({"cable": True}, cl, y, h))
            y += h
        for s in ssids:
            cl = by_if.get((ap.name, s["ifname"]), []) if show_clients else []
            h = max(ROW, len(cl) * CROW + (ROW - CROW))
            children.append((s, cl, y, h))
            y += h
        if not children:
            y += ROW
        block_h = y - block_top
        is_sw = ap.kind == "switch"
        if not ap.online:
            extra = t("map.offline")
        elif is_sw:
            extra = t("map.ports_up", up=sum(1 for p in ap.ports if p["up"]), n=len(ap.ports))
        else:
            extra = t("map.clients", n=ap.client_count)
        apn = {"id": "ap:" + ap.name, "kind": "switch" if is_sw else "ap", "x": COL_X["ap"] + depth * INDENT,
               "y": block_top + (block_h - ROW) / 2 + (ROW - NODE_H) / 2, "depth": depth,
               "w": NODE_W["ap"] - depth * INDENT, "title": ap.name, "sub": ap.host, "online": ap.online, "href": "/ap/" + quote(ap.name),
               "extra": extra}
        nodes.append(apn)
        ap_nodes.append((ap, apn))
        for s, cl, top, h in children:
            if s.get("cable"):
                lanc = nets["lan"]["color"]
                ports = sorted({d["port"] for d in cab}, key=int)
                gn = {"id": f"cable:{ap.name}", "kind": "cable", "x": COL_X["ssid"], "y": top + (h - ROW) / 2 + (ROW - NODE_H) / 2,
                      "w": NODE_W["ssid"], "title": t("map.cable"),
                      "sub": t("map.ports", ports=", ".join(ports)),
                      "extra": t("map.clients", n=len(cab)), "online": True, "color": lanc, "href": ""}
                nodes.append(gn)
                edges.append({"from": apn, "to": gn, "kind": "lan", "color": lanc})
                for k, c in enumerate(cl):
                    cc = nets[_net_key(c["vlan"])]["color"] if "vlan" in c and _net_key(c["vlan"]) in nets else lanc
                    cn = _client_node({**c, "port": c["port"]}, COL_X["client"],
                                      top + (ROW - CROW) / 2 + k * CROW + (CROW - CLIENT_H) / 2, cc, True, t)
                    sp = speed_label(c.get("speed", 0))
                    cn["extra"] = f"port {c['port']}" + (f" · {sp}" if sp else "") + (f" · {c['ip']}" if c.get("ip") else "")
                    cn["tip"] = " · ".join(v for v in [c.get("hostname") or c["mac"], c.get("ip", ""), c["mac"], f"port {c['port']}", sp] if v)
                    if c.get("speed"):
                        cn["speedcls"] = "fast" if c["speed"] >= 1000 else "slow"
                    if c.get("stale"):
                        cn["stale"] = True
                        cn["extra"] = f"port {c['port']} · {_ago(c['stale'], t)}"
                        cn["tip"] += " · " + t("map.last_seen", ago=_ago(c["stale"], t))
                    nodes.append(cn)
                    edges.append({"from": gn, "to": cn, "kind": "cable", "color": cc})
                continue
            n = net_of_ap_network(ap, s.get("network", ""))
            sn = {"id": f"ssid:{ap.name}:{s['ifname']}", "kind": "ssid", "x": COL_X["ssid"],
                  "y": top + (h - ROW) / 2 + (ROW - NODE_H) / 2,
                  "w": NODE_W["ssid"], "title": s["ssid"] or "–", "sub": s.get("band", ""),
                  "extra": t("map.clients", n=s.get("clients", 0)), "online": True, "color": n["color"],
                  "net": n["label"], "href": "/clients?" + urlencode({"ap": ap.name, "ssid": s["ssid"]})}
            nodes.append(sn)
            edges.append({"from": apn, "to": sn, "kind": "wifi", "color": n["color"]})
            for k, c in enumerate(cl):
                cn = _client_node(c, COL_X["client"], top + (ROW - CROW) / 2 + k * CROW + (CROW - CLIENT_H) / 2,
                                  n["color"], False, t)
                nodes.append(cn)
                edges.append({"from": sn, "to": cn, "kind": "client", "color": n["color"]})

    # kabelova zarizeni u routeru (mimo Wi-Fi a porty AP), rozdelena podle site (podsit rozhrani routeru)
    groups: dict[str, list[dict]] = {}
    for c in wired:
        vlan = c.get("vlan")
        k = _net_key(vlan)
        if k not in nets:
            net(vlan, c.get("iface", ""))
            nets[k].update(color=PALETTE[len(nets) % len(PALETTE)], label=f"VLAN {vlan}", names_str=c.get("iface", ""))
        groups.setdefault(k, []).append(c)
    wired_nodes = []
    for k in sorted(groups, key=lambda k: (nets[k]["vlan"] is not None, nets[k]["vlan"] or 0)):
        n = nets[k]
        cl = sorted(groups[k], key=lambda c: tuple(int(p) if p.isdigit() else 0 for p in (c.get("ip") or "0").split("."))) \
            if show_clients else []
        h = max(ROW, len(cl) * CROW + (ROW - CROW))
        title = t("map.wired") if k == "lan" else n["label"] + (f" · {n['names_str']}" if n.get("names_str") else "")
        wn = {"id": "wired:" + k, "kind": "wired", "x": COL_X["ap"], "y": y + (h - ROW) / 2 + (ROW - NODE_H) / 2,
              "w": NODE_W["wired"], "title": title, "sub": t("map.wired_sub"),
              "extra": t("map.clients", n=len(groups[k])), "online": True, "color": n["color"],
              "href": ("/ap/" + quote(router.name)) if router else ""}
        nodes.append(wn)
        wired_nodes.append(wn)
        for j, c in enumerate(cl):
            cn = _client_node(c, COL_X["ssid"], y + (ROW - CROW) / 2 + j * CROW + (CROW - CLIENT_H) / 2, n["color"], True, t)
            nodes.append(cn)
            edges.append({"from": wn, "to": cn, "kind": "cable", "color": n["color"]})
        y += h

    height = max(y, TOP + ROW) + 10
    mid = (TOP + y) / 2 - NODE_H / 2

    # ---- router / virtualni LAN
    if router:
        rn = {"id": "router", "kind": "router", "x": COL_X["router"], "y": mid, "w": NODE_W["router"],
              "title": router.name, "sub": router.host, "online": router.online, "href": "/ap/" + quote(router.name),
              "extra": ""}
    else:
        rn = {"id": "router", "kind": "lan", "x": COL_X["router"], "y": mid, "w": NODE_W["router"],
              "title": t("map.lan"), "sub": t("map.no_router"), "online": True, "href": "", "extra": ""}
    nodes.append(rn)

    if router:
        wan = next((i for i in router.interfaces if i.get("wan") and i.get("up")), None)
        inet = {"id": "internet", "kind": "internet", "x": COL_X["internet"], "y": mid, "w": NODE_W["internet"],
                "title": t("map.internet"), "sub": (wan or {}).get("ipv4", "") or "", "online": bool(wan),
                "href": "", "extra": ""}
        nodes.append(inet)
        edges.append({"from": inet, "to": rn, "kind": "wan", "color": "", "up": bool(wan)})

    lan_color = nets["lan"]["color"]
    ap_by_name = {a.name: n for a, n in ap_nodes}
    for ap, apn in ap_nodes:
        vl = sorted({v for v in ap_ifvlan.get(ap.name, {}).values() if v is not None})
        if ap.kind == "switch":  # VLANy z portu switche (Q-BRIDGE-MIB), jen site, ktere zna router
            vl = sorted({v for p in ap.ports for v in [*p.get("vlans_t", []), *p.get("vlans_u", [])]
                         if _net_key(v) in nets})
        chips = [{"label": nets[_net_key(v)]["label"], "color": nets[_net_key(v)]["color"]} for v in vl]
        apn["chips"] = _chips(apn, chips)
        src = ap_by_name.get(parent.get(ap.name)) or rn
        edges.append({"from": src, "to": apn, "kind": "trunk" if vl else "lan", "color": lan_color, "up": ap.online,
                      "via": src is not rn, "speed": speed_label(speeds.get(ap.name, 0)) if ap.online else "",
                      "speedcls": ("fast" if speeds.get(ap.name, 0) >= 1000 else "slow") if speeds.get(ap.name) else ""})
    if router:
        rv = sorted({v for v in router_vl.values() if v is not None})
        rn["chips"] = _chips(rn, [{"label": nets[_net_key(v)]["label"], "color": nets[_net_key(v)]["color"]} for v in rv])
    for wn in wired_nodes:
        edges.append({"from": rn, "to": wn, "kind": "lan", "color": wn["color"], "chips": [], "up": True})

    # ---- kazde zarizeni zapojene pres jine ma vlastni svislou caru: nizsi potomek vlevo (cary se nekrizi)
    via_x: dict[int, float] = {}
    by_parent: dict[str, list[dict]] = {}
    for e in edges:
        if e.get("via"):
            by_parent.setdefault(e["from"]["id"], []).append(e)
    for lst in by_parent.values():
        lst.sort(key=lambda e: e["to"]["y"])
        n = len(lst)
        gap = min(14.0, (INDENT - 24) / max(1, n - 1))
        for k, e in enumerate(lst):
            via_x[id(e)] = 10 + (n - 1 - k) * gap

    # ---- geometrie spojnic (bezier z praveho okraje rodice do leveho okraje potomka)
    for e in edges:
        a, b = e["from"], e["to"]
        if e.get("via"):  # AP/switch zapojeny pres jine zarizeni: vlastni lomena cara z dolni hrany nadrazeneho
            x1, y1 = a["x"] + via_x.get(id(e), 18), a["y"] + a.get("h", NODE_H)
            x2, y2 = b["x"], b["y"] + b.get("h", NODE_H) / 2
            r = min(12, (y2 - y1) / 2, (x2 - x1) / 2) if y2 > y1 and x2 > x1 else 0
            e["d"] = f"M{x1:.0f},{y1:.0f} V{y2 - r:.0f} Q{x1:.0f},{y2:.0f} {x1 + r:.0f},{y2:.0f} H{x2:.0f}"
            e["mx"], e["my"] = x1, y2
            e["lx"], e["ly"] = x2 - 6, y2 - 5
            continue
        x1, y1 = a["x"] + a["w"], a["y"] + a.get("h", NODE_H) / 2
        x2, y2 = b["x"], b["y"] + b.get("h", NODE_H) / 2
        dx = (x2 - x1) / 2
        e["d"] = f"M{x1:.0f},{y1:.0f} C{x1 + dx:.0f},{y1:.0f} {x2 - dx:.0f},{y2:.0f} {x2:.0f},{y2:.0f}"
        e["mx"], e["my"] = (x1 + x2) / 2, (y1 + y2) / 2
        e["lx"], e["ly"] = x2 - 6, y2 - 5

    for n in nodes:
        n.setdefault("h", NODE_H)
    has_clients = show_clients and any(n["kind"] == "client" and n["x"] == COL_X["client"] for n in nodes)
    width = (COL_X["client"] + NODE_W["client"] if has_clients else COL_X["ssid"] + NODE_W["ssid"]) + 20
    return {"nodes": nodes, "edges": edges, "width": width, "height": height, "node_h": NODE_H,
            "networks": order, "hidden_ssids": hidden}
