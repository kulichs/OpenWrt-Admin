"""Made-up data for demo mode (UI development without real APs)."""
import math
import random
import time

from .collector import APState

T0 = time.time()


def _clients(ap, ssid, net, ifname, n, freq=2437):
    out = []
    now = time.time()
    for i in range(n):
        # stabilni MAC a rostouci citace bajtu (aktualni rychlost klientu se pocita z rozdilu mezi nactenimi)
        r = random.Random(f"{ap}|{ifname}|{i}")
        mac = "02:00:%02x:%02x:%02x:%02x" % tuple(r.randrange(256) for _ in range(4))
        speed = r.choice([2e3, 2e4, 1e5, 6e5, 3e6])  # B/s
        rx0, tx0 = r.randint(10**5, 10**9), r.randint(10**5, 10**9)
        out.append({
            "mac": mac, "ap": ap, "ssid": ssid, "network": net, "ifname": ifname,
            "signal": random.randint(-82, -38), "rx_rate": random.choice([65000, 72200, 144400, 866700]),
            "tx_rate": random.choice([58500, 72200, 130000, 780000]),
            "rx_bytes": int(rx0 + speed * 0.2 * (now - T0 + 80 * math.sin((now - T0) / 120))),
            "tx_bytes": int(tx0 + speed * (now - T0 + 80 * math.sin((now - T0) / 120))),
            "connected": random.randint(30, 200000), "freq": freq,
            "band": "5 GHz" if freq >= 4900 else "2,4 GHz",
            "standard": random.choice(["Wi-Fi 5 (ac)", "Wi-Fi 6 (ax)", "Wi-Fi 4 (n)"]) if freq >= 4900
                        else random.choice(["Wi-Fi 4 (n)", "Wi-Fi 4 (n)", "802.11b/g"]),
        })
    return out


def demo_states() -> tuple[list[APState], dict]:
    now = time.time()
    a = APState(name="AP-Living", host="192.168.1.2", online=True, updated=now,
                hostname="ap-living", model="Ubiquiti UniFi AP LR",
                release="OpenWrt 25.12.2 r30000", kernel="6.6.110", uptime=7340, load=0.08,
                mem_total=60 * 2**20, mem_avail=31 * 2**20,
                overlay_size=1600 * 1024, overlay_used=420 * 1024, overlay_avail=1180 * 1024)
    a.radios = [{"name": "radio0", "up": True, "disabled": False, "channel": 6, "htmode": "HT20",
                 "band": "2,4 GHz", "txpower": "auto", "country": "CZ"}]
    specs = [("phy0-ap0", "HomeWiFi", "lan", 3), ("phy0-ap1", "HomeWiFi-Guest", "guest", 1),
             ("phy0-ap2", "HomeWiFi-IoT", "iot", 6)]
    for ifn, ssid, net, n in specs:
        cl = _clients(a.name, ssid, net, ifn, n)
        a.clients += cl
        a.ssids.append({"ifname": ifn, "ssid": ssid, "network": net, "radio": "radio0",
                        "encryption": "psk2+ccmp", "channel": 6, "freq": 2437, "band": "2,4 GHz", "clients": n})

    b = APState(name="AP-Office", host="192.168.1.3", online=True, updated=now,
                hostname="ap-office", model="Xiaomi Mi Router 4A Gigabit",
                release="OpenWrt 25.12.2 r30000", kernel="6.6.110", uptime=950000, load=0.21,
                mem_total=120 * 2**20, mem_avail=70 * 2**20,
                overlay_size=12 * 2**20, overlay_used=3 * 2**20, overlay_avail=9 * 2**20)
    b.radios = [{"name": "radio0", "up": True, "disabled": False, "channel": 1, "htmode": "HT20",
                 "band": "2,4 GHz", "txpower": 20, "country": "CZ"},
                {"name": "radio1", "up": True, "disabled": False, "channel": 36, "htmode": "VHT80",
                 "band": "5 GHz", "txpower": "auto", "country": "CZ"}]
    specs = [("phy0-ap0", "HomeWiFi", "lan", "radio0", 2), ("phy1-ap0", "HomeWiFi", "lan", "radio1", 4),
             ("phy0-ap1", "HomeWiFi-IoT", "iot", "radio0", 5), ("phy0-ap2", "HomeWiFi-Guest", "guest", "radio0", 0)]
    for ifn, ssid, net, radio, n in specs:
        cl = _clients(b.name, ssid, net, ifn, n, 2412 if radio == "radio0" else 5180)
        b.clients += cl
        b.ssids.append({"ifname": ifn, "ssid": ssid, "network": net, "radio": radio,
                        "encryption": "psk2+ccmp", "channel": 1 if radio == "radio0" else 36,
                        "freq": 2412 if radio == "radio0" else 5180,
                        "band": "2,4 GHz" if radio == "radio0" else "5 GHz", "clients": n})

    c = APState(name="AP-Garage", host="192.168.1.4", online=False, updated=now,
                error="TimeoutError: connect timed out")

    for ap in (a, b):
        ap.interfaces = [
            {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "ipv4": ap.host + "/24", "ipv6": "",
             "gateway": "", "dns": "", "uptime": ap.uptime, "wan": False, "vlan": None},
            {"name": "guest", "up": True, "proto": "none", "device": "br-guest", "ipv4": "", "ipv6": "",
             "gateway": "", "dns": "", "uptime": ap.uptime, "wan": False, "vlan": 30},
            {"name": "iot", "up": True, "proto": "none", "device": "br-iot", "ipv4": "", "ipv6": "",
             "gateway": "", "dns": "", "uptime": ap.uptime, "wan": False, "vlan": 10},
        ]
    for ap in (a, b):
        ap.pkg = "apk"
        ap.wifi_ifaces = [{"section": f"wifi_{s['ifname'].replace('-', '_')}", "device": s["radio"], "mode": "ap",
                           "ssid": s["ssid"], "network": s["network"], "encryption": "psk2+ccmp",
                           "has_key": True, "hidden": False, "disabled": False} for s in ap.ssids]
    b.wifi_ifaces.append({"section": "guest5", "device": "radio1", "mode": "ap", "ssid": "HomeWiFi-Guest",
                          "network": "guest", "encryption": "psk2", "has_key": True, "hidden": False,
                          "disabled": True})

    # skutecny stav radii (iwinfo) a citace survey (vytizeni kanalu); AP-Office 2,4 GHz schvalne na kanalu 3
    b.radios[0]["channel"] = 3
    for ap, r, util, tx, noise, hw in [(a, a.radios[0], 0.34, 20, -92, "Atheros AR9342"),
                                        (b, b.radios[0], 0.22, 17, -95, "MediaTek MT7628"),
                                        (b, b.radios[1], 0.06, 23, -97, "MediaTek MT7612E")]:
        active = (now - T0) * 1000 + 5e8
        ch = int(r["channel"])
        r.update(ifnames=sorted(s["ifname"] for s in ap.ssids if s["radio"] == r["name"]),
                 phy="phy" + r["name"][-1], act_channel=ch, act_htmode=r["htmode"], act_txpower=tx, noise=noise, hw=hw,
                 freq=(2407 + 5 * ch) if ch < 14 else (5000 + 5 * ch),
                 txpowers=list(range(0, 21 if ch < 14 else 24)),
                 survey={"active": int(active), "busy": int(active * util + 4e5 * math.sin((now - T0) / 300)),
                         "rx": int(active * util * 0.7), "tx": int(active * util * 0.2)})

    leases = {}
    names = ["phone-anna", "laptop", "esp-thermo", "tv-living", "shelly-1", "tablet"]
    for i, cl in enumerate(a.clients + b.clients):
        if i % 3 != 2:
            leases[cl["mac"]] = {"ip": f"192.168.1.{100 + i}", "hostname": names[i % len(names)]}
    # switch v AP-Office: uplink port 2 (router), CPU port 0, NAS a tiskarna na portu 3, AP-Living zapojeny pres port 4
    router_mac, living_mac = "02:00:00:00:00:01", "02:00:00:00:00:12"
    a.mac, b.mac = living_mac, "02:00:00:00:00:13"
    for ap in (a, b, c):
        ap.arp = {"192.168.1.1": router_mac, "192.168.30.1": router_mac}
    b.switch_vlans = ["0t 2 3 4", "0t 2t", "0t 2t"]
    b.arl = {"switch0:0": [b.mac] + [x["mac"] for x in b.clients],
             "switch0:2": [router_mac, "02:00:00:00:10:02"],
             "switch0:3": ["02:00:00:00:10:01", "02:00:00:00:10:03", "02:00:00:00:10:05"],
             "switch0:4": [living_mac] + [x["mac"] for x in a.clients]}
    b.arp["192.168.1.50"] = "02:00:00:00:10:05"
    # rychlosti portu: AP-Office switch (swconfig), AP-Living jen eth0 na 100 Mb/s (spatny kabel)
    b.ports = [{"id": f"switch0:{n}", "num": str(n), "up": up, "speed": sp, "duplex": "full" if up else "", "kind": "switch"}
               for n, up, sp in [(0, True, 1000), (1, False, 0), (2, True, 1000), (3, True, 100), (4, True, 100), (5, False, 0)]]
    a.ports = [{"id": "eth0", "num": "eth0", "up": True, "speed": 100, "duplex": "full", "kind": "dev"}]

    # zarizeni na kabelu (jen v DHCP routeru)
    for i, (mac, name) in enumerate([("02:00:00:00:10:01", "nas"), ("02:00:00:00:10:02", "homeassistant"),
                                     ("02:00:00:00:10:03", "printer"), ("02:00:00:00:10:04", "")]):
        leases[mac] = {"ip": f"192.168.1.{10 + i}", "hostname": name}
    return [a, b, c], leases


def demo_router(name: str, host: str) -> APState:
    r = APState(name=name, host=host, kind="router", online=True, updated=time.time(),
                hostname="router", model="Xunlong Orange Pi R1 Plus LTS",
                release="OpenWrt 25.12.2 r30000", kernel="6.6.110", uptime=1_950_000, load=0.12,
                mem_total=990 * 2**20, mem_avail=760 * 2**20,
                overlay_size=6 * 2**30, overlay_used=310 * 2**20, overlay_avail=5600 * 2**20, pkg="apk")
    r.interfaces = [
        {"name": "wan", "up": True, "proto": "dhcp", "device": "eth0", "ipv4": "203.0.113.25/24", "ipv6": "",
         "gateway": "203.0.113.1", "dns": "203.0.113.53", "uptime": 1_949_000, "wan": True, "vlan": None},
        {"name": "lan", "up": True, "proto": "static", "device": "br-lan", "ipv4": "192.168.1.1/24",
         "ipv6": "fd00:1::1/60", "gateway": "", "dns": "", "uptime": 1_950_000, "wan": False, "vlan": None},
        {"name": "guest", "up": True, "proto": "static", "device": "br-lan.30", "ipv4": "192.168.30.1/24",
         "ipv6": "", "gateway": "", "dns": "", "uptime": 1_950_000, "wan": False, "vlan": 30},
        {"name": "iot", "up": True, "proto": "static", "device": "br-lan.10", "ipv4": "192.168.10.1/24",
         "ipv6": "", "gateway": "", "dns": "", "uptime": 1_950_000, "wan": False, "vlan": 10},
        {"name": "dmz", "up": True, "proto": "static", "device": "eth1.100", "ipv4": "192.168.100.1/24",
         "ipv6": "", "gateway": "", "dns": "", "uptime": 1_950_000, "wan": False, "vlan": 100},
        {"name": "wg0", "up": True, "proto": "wireguard", "device": "wg0", "ipv4": "10.9.0.1/24",
         "ipv6": "", "gateway": "", "dns": "", "uptime": 1_950_000, "wan": False, "vlan": None},
    ]
    r.ports = [{"id": "eth0", "num": "eth0", "up": True, "speed": 1000, "duplex": "full", "kind": "dev"},
               {"id": "eth1", "num": "eth1", "up": True, "speed": 1000, "duplex": "full", "kind": "dev"}]
    # server v DMZ (pevna IP, bez DHCP) a zarizeni ve VLAN IoT na kabelu u routeru
    r.arp = {"192.168.100.7": "02:00:00:00:30:01", "192.168.10.20": "02:00:00:00:30:02"}
    r.arp_dev = {"192.168.100.7": "eth1.100", "192.168.10.20": "br-lan.10"}
    r.static_hosts = {}
    return r


def demo_switch(name: str, host: str, aps: list[APState], router_mac: str) -> APState:
    """Spravovany switch (SNMP) mezi routerem a AP: port 24 router, port 2 AP-Office (a vse za nim),
    port 5 Home Assistant, port 8 server v DMZ (100 Mb/s), port 6 zarizeni ve VLAN IoT."""
    sw = APState(name=name, host=host, kind="switch", online=True, updated=time.time(),
                 model="GS724Tv4 ProSafe 24-port Gigabit Ethernet Smart Switch", release="6.3.1.47, B1.0.0.4",
                 uptime=3_210_000, mac="02:00:00:00:00:50")
    office = next((a for a in aps if a.name == "AP-Office"), None)
    behind, living = [], []
    if office:
        living = office.arl.pop("switch0:4", [])  # se switchem je AP-Living zapojeny primo do nej (port 3)
        office.ports = [p for p in office.ports if p["num"] != "4"]
        behind = [office.mac] + [m for p, macs in office.arl.items() if p != "switch0:2" for m in macs]
    sw.arl = {"sw:24": [router_mac], "sw:2": sorted(set(behind)), "sw:3": sorted(set(living)), "sw:5": ["02:00:00:00:10:02"],
              "sw:8": ["02:00:00:00:30:01"], "sw:6": ["02:00:00:00:30:02"], "sw:51": [sw.mac]}
    speeds = {2: 1000, 3: 100, 5: 1000, 6: 1000, 8: 100, 24: 1000}
    sw.ports = [{"id": f"sw:{n}", "num": str(n), "up": n in speeds, "speed": speeds.get(n, 0),
                 "duplex": "full" if n in speeds else "", "kind": "switch",
                 "name": f"g{n}", "ifindex": n, "alias": {24: "router", 2: "AP-Office", 3: "AP-Living", 8: "dmz"}.get(n, ""),
                 "vlans_u": [100] if n == 8 else [1], "vlans_t": [10, 30] if n in (2, 3, 24) else []} for n in range(1, 25)]
    return sw


DEMO_NEIGHBORS = [  # (essid, kanal, sirka, posun, signal)
    ("Neighbor-5G", 36, 40, "above", -52), ("FamilyNet", 40, 80, "below", -81), ("Office-Guest", 60, 40, "below", -83),
    ("", 112, 40, "above", -74), ("Neighbor", 11, 40, "below", -58), ("PrinterDirect", 6, 20, "", -66),
    ("CafeWiFi", 4, 40, "above", -78), ("", 3, 40, "above", -77), ("IoT-Hub", 11, 20, "", -85), ("Garden", 1, 20, "", -88),
]


def demo_scan(radio: dict) -> list[dict]:
    """Vymysleny sken okoli pro demo (stejny tvar jako radios.parse_scan)."""
    b24 = str(radio.get("band", "")).startswith("2")
    out = []
    for i, (essid, ch, w, off, sig) in enumerate(DEMO_NEIGHBORS):
        if (ch <= 14) != b24:
            continue
        out.append({"bssid": f"02:aa:00:00:{i:02x}:01", "essid": essid, "channel": ch,
                    "freq": (2407 + 5 * ch) if ch <= 14 else 5000 + 5 * ch, "band": "2,4 GHz" if ch <= 14 else "5 GHz",
                    "signal": sig + random.randint(-3, 3), "enc": "WPA2 PSK (CCMP)", "width": w, "offset": off})
    return sorted(out, key=lambda c: -c["signal"])
