"""Mapa site: uzly (internet, router, AP, SSID, kabelova zarizeni) a spojnice (WAN, LAN/trunk s VLANy, Wi-Fi).

Fyzicke zapojeni (switche, porty) z ubus nevidime. Spojnice router -> AP znamena "AP je ve stejne
siti a nese tyto site/VLANy", ne konkretni kabel. VLAN ID se bere z nazvu zarizeni (eth0.30,
br-lan.10) nebo z clenu bridge (br-host -> eth0.30), netagovana sit = LAN.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from .collector import APState

PALETTE = ["#03a9f4", "#8e44ad", "#e67e22", "#16a085", "#c0392b", "#2c7be5", "#b7950b", "#7f8c8d"]

COL_X = {"internet": 20, "router": 225, "ap": 480, "ssid": 830}
NODE_W = {"internet": 170, "router": 190, "ap": 280, "ssid": 250, "wired": 280}
NODE_H = 52
ROW = 66
TOP = 20


def _net_key(vlan: int | None) -> str:
    return "lan" if vlan is None else f"vlan{vlan}"


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
    if router:
        for i in router.interfaces:
            if not i.get("wan"):
                net(i.get("vlan"), i["name"])
    ap_ifvlan: dict[str, dict[str, int | None]] = {}
    for ap in aps:
        m = {}
        for i in ap.interfaces:
            if not i.get("wan"):
                m[i["name"]] = i.get("vlan")
                net(i.get("vlan"), i["name"])
        ap_ifvlan[ap.name] = m

    order = sorted(nets.values(), key=lambda n: (n["vlan"] is not None, n["vlan"] or 0))
    for idx, n in enumerate(order):
        n["color"] = PALETTE[idx % len(PALETTE)]
        n["label"] = (t("map.lan") if n["vlan"] is None else f"VLAN {n['vlan']}")
        n["names_str"] = ", ".join(n["names"])

    def net_of_ap_network(ap: APState, network: str) -> dict:
        # SSID muze byt ve vice sitich ("lan, iot") -> prvni
        first = (network or "").split(",")[0].strip()
        vlan = ap_ifvlan.get(ap.name, {}).get(first)
        if first and first not in ap_ifvlan.get(ap.name, {}) and not ap.interfaces:
            # AP bez dat o rozhranich: zkus podle jmena site na routeru
            for n in nets.values():
                if first in n["names"]:
                    return n
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
        apn["chips"] = [{**c, "x": apn["x"] + apn["w"] - 10 - (len(chips) - k) * 54, "y": apn["y"] + 28, "w": 50}
                        for k, c in enumerate(chips)]
        edges.append({"from": rn, "to": apn, "kind": "trunk" if vl else "lan", "color": lan_color, "up": ap.online})
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
