"""Mapa site: uzly (internet, router, AP, SSID, kabelova zarizeni) a spojnice (WAN, LAN/trunk s VLANy, Wi-Fi).

Fyzicke zapojeni (switche, porty) z ubus nevidime. Spojnice router -> AP znamena "AP je ve stejne
siti a nese tyto site/VLANy", ne konkretni kabel. VLAN ID se bere z nazvu zarizeni (eth0.30,
br-lan.10) nebo z clenu bridge (br-host -> eth0.30), netagovana sit = LAN.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from .collector import APState

PALETTE = ["#03a9f4", "#8e44ad", "#e67e22", "#16a085", "#c0392b", "#2c7be5", "#b7950b", "#7f8c8d"]

COL_X = {"internet": 20, "router": 225, "ap": 530, "ssid": 880}
NODE_W = {"internet": 170, "router": 240, "ap": 280, "ssid": 250, "wired": 280}
NODE_H = 52
ROW = 66
TOP = 20


def _chips(node: dict, chips: list[dict]) -> list[dict]:
    """Stitky VLAN vpravo dole v uzlu; kdyz se nevejdou vedle IP, zuzi se na samotne cislo."""
    if not chips:
        return []
    room = node["w"] - 120  # vlevo je IP adresa
    w, gap = 48, 4
    if len(chips) * (w + gap) > room:
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
        if i.get("wan"):
            continue
        mgmt = i["name"] == "lan" or any(a.split("/")[0] == host for a in (i.get("ipv4") or "").split(", ") if a)
        out[i["name"]] = None if mgmt else i.get("vlan")
    return out


def build(router: APState | None, aps: list[APState], wired_count: int, t) -> dict:
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

    # ---- rozlozeni: kazdy AP zabira tolik radku, kolik ma SSID (min. 1)
    nodes, edges = [], []
    y = TOP
    ap_nodes = []
    for ap in aps:
        ssids = ap.ssids if ap.online else []
        rows = max(1, len(ssids))
        block_top = y
        ap_y = block_top + (rows - 1) * ROW / 2
        apn = {"id": "ap:" + ap.name, "kind": "ap", "x": COL_X["ap"], "y": ap_y, "w": NODE_W["ap"],
               "title": ap.name, "sub": ap.host, "online": ap.online, "href": "/ap/" + quote(ap.name),
               "extra": t("map.clients", n=ap.client_count) if ap.online else t("map.offline")}
        nodes.append(apn)
        ap_nodes.append((ap, apn))
        for k, s in enumerate(ssids):
            n = net_of_ap_network(ap, s.get("network", ""))
            sn = {"id": f"ssid:{ap.name}:{s['ifname']}", "kind": "ssid", "x": COL_X["ssid"], "y": block_top + k * ROW,
                  "w": NODE_W["ssid"], "title": s["ssid"] or "–", "sub": s.get("band", ""),
                  "extra": t("map.clients", n=s.get("clients", 0)), "online": True, "color": n["color"],
                  "net": n["label"], "href": "/clients?" + urlencode({"ap": ap.name, "ssid": s["ssid"]})}
            nodes.append(sn)
            edges.append({"from": apn, "to": sn, "kind": "wifi", "color": n["color"]})
        y = block_top + rows * ROW

    wired = None
    if router or wired_count:
        wired = {"id": "wired", "kind": "wired", "x": COL_X["ap"], "y": y, "w": NODE_W["wired"],
                 "title": t("map.wired"), "sub": t("map.wired_sub"), "extra": t("map.clients", n=wired_count),
                 "online": True, "href": ("/ap/" + quote(router.name)) if router else ""}
        nodes.append(wired)
        y += ROW

    height = max(y, TOP + ROW) + 10
    mid = (TOP + y - ROW) / 2

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
    for ap, apn in ap_nodes:
        vl = sorted({v for v in ap_ifvlan.get(ap.name, {}).values() if v is not None})
        chips = [{"label": nets[_net_key(v)]["label"], "color": nets[_net_key(v)]["color"]} for v in vl]
        apn["chips"] = _chips(apn, chips)
        edges.append({"from": rn, "to": apn, "kind": "trunk" if vl else "lan", "color": lan_color, "up": ap.online})
    if router:
        rv = sorted({v for v in router_vl.values() if v is not None})
        rn["chips"] = _chips(rn, [{"label": nets[_net_key(v)]["label"], "color": nets[_net_key(v)]["color"]} for v in rv])
    if wired:
        edges.append({"from": rn, "to": wired, "kind": "lan", "color": lan_color, "chips": [], "up": True})

    # ---- geometrie spojnic (bezier z praveho okraje rodice do leveho okraje potomka)
    for e in edges:
        a, b = e["from"], e["to"]
        x1, y1 = a["x"] + a["w"], a["y"] + NODE_H / 2
        x2, y2 = b["x"], b["y"] + NODE_H / 2
        dx = (x2 - x1) / 2
        e["d"] = f"M{x1:.0f},{y1:.0f} C{x1 + dx:.0f},{y1:.0f} {x2 - dx:.0f},{y2:.0f} {x2:.0f},{y2:.0f}"
        e["mx"], e["my"] = (x1 + x2) / 2, (y1 + y2) / 2

    width = COL_X["ssid"] + NODE_W["ssid"] + 20
    return {"nodes": nodes, "edges": edges, "width": width, "height": height, "node_h": NODE_H,
            "networks": order}
