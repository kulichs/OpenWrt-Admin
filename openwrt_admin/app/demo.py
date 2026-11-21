"""Made-up data for demo mode (UI development without real APs)."""
import random
import time

from .collector import APState


def _clients(ap, ssid, net, ifname, n, freq=2437):
    out = []
    for i in range(n):
        mac = "02:00:%02x:%02x:%02x:%02x" % tuple(random.randrange(256) for _ in range(4))
        out.append({
            "mac": mac, "ap": ap, "ssid": ssid, "network": net, "ifname": ifname,
            "signal": random.randint(-82, -38), "rx_rate": random.choice([65000, 72200, 144400, 866700]),
            "tx_rate": random.choice([58500, 72200, 130000, 780000]),
            "rx_bytes": random.randint(10**5, 10**9), "tx_bytes": random.randint(10**5, 10**9),
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
        ap.pkg = "apk"
        ap.wifi_ifaces = [{"section": f"wifi_{s['ifname'].replace('-', '_')}", "device": s["radio"], "mode": "ap",
                           "ssid": s["ssid"], "network": s["network"], "encryption": "psk2+ccmp",
                           "has_key": True, "hidden": False, "disabled": False} for s in ap.ssids]
    b.wifi_ifaces.append({"section": "guest5", "device": "radio1", "mode": "ap", "ssid": "HomeWiFi-Guest",
                          "network": "guest", "encryption": "psk2", "has_key": True, "hidden": False,
                          "disabled": True})

    leases = {}
    names = ["phone-anna", "laptop", "esp-thermo", "tv-living", "shelly-1", "tablet"]
    for i, cl in enumerate(a.clients + b.clients):
        if i % 3 != 2:
            leases[cl["mac"]] = {"ip": f"192.168.1.{100 + i}", "hostname": names[i % len(names)]}
    return [a, b, c], leases
