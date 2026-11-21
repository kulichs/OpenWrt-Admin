"""Sber dat z OpenWrt AP pres SSH (ubus)."""
from __future__ import annotations

import asyncio
import re
import json
import logging
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

import asyncssh

log = logging.getLogger("openwrt_admin.collector")


@dataclass
class APConfig:
    name: str
    host: str
    port: int = 22
    user: str = "root"


@dataclass
class APState:
    name: str
    host: str
    online: bool = False
    error: str | None = None
    updated: float = 0.0
    hostname: str = ""
    model: str = ""
    release: str = ""
    kernel: str = ""
    uptime: int = 0
    load: float = 0.0
    mem_total: int = 0
    mem_avail: int = 0
    overlay_size: int = 0
    overlay_used: int = 0
    overlay_avail: int = 0
    radios: list[dict[str, Any]] = field(default_factory=list)
    ssids: list[dict[str, Any]] = field(default_factory=list)
    clients: list[dict[str, Any]] = field(default_factory=list)
    # wifi-iface sekce z uci (vcetne vypnutych SSID), bez hesel
    wifi_ifaces: list[dict[str, Any]] = field(default_factory=list)
    pkg: str = ""  # "apk" / "opkg"
    kind: str = "ap"  # "ap" / "router"
    # router: rozhrani z `ubus call network.interface dump`
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    # switch (swconfig): MAC adresy podle portu, ARP tabulka, vlastni MAC, porty switch_vlan z uci
    mac: str = ""
    arl: dict[str, list[str]] = field(default_factory=dict)   # "switch0:2" -> [mac, ...]
    arp: dict[str, str] = field(default_factory=dict)          # ip -> mac (jedna MAC muze mit vic IP, napr. router ve VLAN)
    arp_dev: dict[str, str] = field(default_factory=dict)      # ip -> zarizeni (br-lan, eth0.100, ...)
    switch_vlans: list[str] = field(default_factory=list)      # ["0t 2 3 4 5", "0t 2t", ...]
    # router: staticke DHCP rezervace z `uci show dhcp` (host sekce) -> {mac: {name, ip}}
    static_hosts: dict[str, dict] = field(default_factory=dict)
    # zablokovani klienti z hostapd (list_bans): [{mac, ifname, ssid}]
    bans: list[dict[str, Any]] = field(default_factory=list)
    bans_ok: bool = False  # hostapd umi list_bans
    # fyzicke porty: [{id: "switch0:1" / "lan1", num, up, speed (Mb/s, 0 = nezname), duplex ("full"/"half"/""), kind}]
    ports: list[dict[str, Any]] = field(default_factory=list)

    @property
    def client_count(self) -> int:
        return len(self.clients)


# ---------------------------------------------------------------- helpers

async def _run(conn: asyncssh.SSHClientConnection, cmd: str, timeout: float) -> str:
    res = await asyncio.wait_for(conn.run(cmd, check=False), timeout)
    if res.exit_status != 0:
        return ""
    out = res.stdout
    return out if isinstance(out, str) else (out or b"").decode(errors="replace")


async def _ubus(conn, path: str, method: str, timeout: float, args: dict | None = None):
    cmd = f"ubus call {shlex.quote(path)} {method}"
    if args is not None:
        cmd += " " + shlex.quote(json.dumps(args))
    out = await _run(conn, cmd, timeout)
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        log.debug("Neplatny JSON z %s %s", path, method)
        return None


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def band_of(freq) -> str:
    try:
        f = int(freq)
    except (TypeError, ValueError):
        return ""
    if f >= 5925:
        return "6 GHz"
    if f >= 4900:
        return "5 GHz"
    if f >= 2400:
        return "2,4 GHz"
    return ""


BAND_NAMES = {
    "2g": "2,4 GHz", "11b": "2,4 GHz", "11g": "2,4 GHz", "11ng": "2,4 GHz", "11bg": "2,4 GHz",
    "5g": "5 GHz", "11a": "5 GHz", "11na": "5 GHz", "11ac": "5 GHz",
    "6g": "6 GHz", "60g": "60 GHz",
}


def norm_band(v) -> str:
    """Pasmo radia z uci: novejsi `band` (2g/5g/6g) i starsi `hwmode` (11g/11a) -> '2,4 GHz' apod."""
    v = str(v or "").strip()
    return BAND_NAMES.get(v.lower(), v)


def wifi_standard(c: dict, freq) -> str:
    """Nejvyssi standard, ktery klient s AP vyjednal (hostapd flagy he/vht/ht)."""
    if c.get("eht"):
        return "Wi-Fi 7 (be)"
    if c.get("he"):
        return "Wi-Fi 6 (ax)"
    if c.get("vht"):
        return "Wi-Fi 5 (ac)"
    if c.get("ht"):
        return "Wi-Fi 4 (n)"
    try:
        return "802.11a" if int(freq) >= 4900 else "802.11b/g"
    except (TypeError, ValueError):
        return "legacy"


MAC_ANY_RE = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")


def parse_bans(data) -> list[str]:
    """hostapd `list_bans` -> MAC adresy (format se mezi verzemi lisi, bere se kazda MAC v odpovedi)."""
    if not data:
        return []
    return sorted({m.lower() for m in MAC_ANY_RE.findall(json.dumps(data))})


def parse_uci_show(text: str) -> dict[str, dict]:
    """Vystup `uci show <config>` -> {sekce: {".type": typ, volba: hodnota nebo list}}."""
    secs: dict[str, dict] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        path, val = line.split("=", 1)
        parts = path.split(".")
        try:
            words = shlex.split(val)
        except ValueError:
            words = [val]
        if len(parts) == 2:
            secs.setdefault(parts[1], {})[".type"] = val
        elif len(parts) == 3:
            secs.setdefault(parts[1], {})[parts[2]] = words[0] if len(words) == 1 else words
    return secs


def parse_wifi_ifaces(text: str) -> list[dict]:
    """wifi-iface sekce z `uci show wireless` (heslo se neuklada)."""
    out = []
    for name, sec in parse_uci_show(text or "").items():
        if sec.get(".type") != "wifi-iface":
            continue
        out.append({
            "section": name,
            "device": sec.get("device", ""),
            "mode": sec.get("mode", ""),
            "ssid": sec.get("ssid", ""),
            "network": ", ".join(_as_list(sec.get("network"))),
            "encryption": sec.get("encryption", ""),
            "has_key": bool(sec.get("key")),
            "hidden": sec.get("hidden") in ("1", "true", "on", "yes"),
            "disabled": sec.get("disabled") in ("1", "true", "on", "yes"),
        })
    return sorted(out, key=lambda i: (i["ssid"], i["device"]))


async def _read_system(conn, st: APState, timeout: float) -> None:
    """Spolecne pro AP i router: model, verze, uptime, RAM, overlay, spravce balicku."""
    board, info, df_out, pkg = await asyncio.gather(
        _ubus(conn, "system", "board", timeout),
        _ubus(conn, "system", "info", timeout),
        _run(conn, "df -k /overlay 2>/dev/null | tail -n 1", timeout),
        _run(conn, "command -v apk >/dev/null && echo apk || (command -v opkg >/dev/null && echo opkg)", timeout),
    )
    st.pkg = pkg.strip()

    if board:
        st.hostname = board.get("hostname", "")
        st.model = board.get("model", "")
        st.kernel = board.get("kernel", "")
        rel = board.get("release") or {}
        st.release = rel.get("description") or rel.get("version", "")
    if info:
        st.uptime = int(info.get("uptime", 0))
        load = info.get("load") or [0]
        st.load = round(load[0] / 65536, 2)
        mem = info.get("memory") or {}
        st.mem_total = int(mem.get("total", 0))
        st.mem_avail = int(mem.get("available", mem.get("free", 0)))
    parts = df_out.split()
    if len(parts) >= 4 and parts[1].isdigit():
        st.overlay_size = int(parts[1]) * 1024
        st.overlay_used = int(parts[2]) * 1024
        st.overlay_avail = int(parts[3]) * 1024


# ---------------------------------------------------------------- AP

async def collect_ap(cfg: APConfig, key: asyncssh.SSHKey, timeout: float = 10.0) -> APState:
    st = APState(name=cfg.name, host=cfg.host)
    try:
        async with asyncssh.connect(
            cfg.host,
            port=cfg.port,
            username=cfg.user,
            client_keys=[key],
            known_hosts=None,  # host klice se meni pri sysupgrade -n; jen LAN a jen cteni
            connect_timeout=timeout,
        ) as conn:
            (wstatus, hostapd_list, uci_wl, ifdump, devstatus, ucinet, arl, arp, swports), _ = await asyncio.gather(
                asyncio.gather(
                    _ubus(conn, "network.wireless", "status", timeout),
                    _run(conn, "ubus list 'hostapd.*'", timeout),
                    _run(conn, "uci -q show wireless", timeout),
                    _ubus(conn, "network.interface", "dump", timeout),
                    _ubus(conn, "network.device", "status", timeout),
                    _run(conn, "uci -q show network", timeout),
                    _run(conn, ARL_CMD, timeout),
                    _run(conn, "cat /proc/net/arp", timeout),
                    _run(conn, PORTS_CMD, timeout),
                ),
                _read_system(conn, st, timeout),
            )
            st.arl, st.arp, st.switch_vlans = parse_arl(arl), parse_arp(arp), switch_vlan_ports(ucinet)
            st.wifi_ifaces = parse_wifi_ifaces(uci_wl)
            st.interfaces = parse_interfaces(ifdump, devstatus, uci_vlans(ucinet), gw_is_wan=False)
            st.ports = merge_ports(parse_swconfig_ports(swports), parse_dev_ports(devstatus, used_devices(devstatus, st.interfaces)),
                                   st.switch_vlans)
            lan_dev = next((i["device"] for i in st.interfaces if i["name"] == "lan"), "br-lan")
            st.mac = (((devstatus or {}).get(lan_dev) or {}).get("macaddr") or "").lower()

            # mapovani ifname -> SSID / sit / radio z netifd
            iface_meta: dict[str, dict] = {}
            for rname, r in (wstatus or {}).items():
                rc = r.get("config") or {}
                st.radios.append({
                    "name": rname,
                    "up": bool(r.get("up")),
                    "disabled": bool(r.get("disabled")),
                    "channel": rc.get("channel", ""),
                    "htmode": rc.get("htmode", ""),
                    "band": norm_band(rc.get("band") or rc.get("hwmode")),
                    "txpower": rc.get("txpower", "auto"),
                    "country": rc.get("country", ""),
                })
                for itf in r.get("interfaces") or []:
                    ic = itf.get("config") or {}
                    ifname = itf.get("ifname")
                    if not ifname:
                        continue
                    iface_meta[ifname] = {
                        "radio": rname,
                        "ssid": ic.get("ssid", ""),
                        "network": ", ".join(_as_list(ic.get("network"))),
                        "encryption": ic.get("encryption", ""),
                        "hidden": bool(ic.get("hidden")),
                        "disabled": bool(ic.get("disabled")),
                    }

            for obj in hostapd_list.split():
                ifname = obj.split(".", 1)[1] if "." in obj else obj
                status, clients, bans = await asyncio.gather(
                    _ubus(conn, obj, "get_status", timeout),
                    _ubus(conn, obj, "get_clients", timeout),
                    _ubus(conn, obj, "list_bans", timeout),
                )
                meta = iface_meta.get(ifname, {})
                st.bans_ok = st.bans_ok or bans is not None
                for bmac in parse_bans(bans):
                    st.bans.append({"mac": bmac, "ifname": ifname,
                                    "ssid": (status or {}).get("ssid") or meta.get("ssid", "")})
                freq = (status or {}).get("freq") or (clients or {}).get("freq") or 0
                ssid = (status or {}).get("ssid") or meta.get("ssid", "")
                cl = (clients or {}).get("clients") or {}
                st.ssids.append({
                    "ifname": ifname,
                    "ssid": ssid,
                    "network": meta.get("network", ""),
                    "radio": meta.get("radio", ""),
                    "encryption": meta.get("encryption", ""),
                    "channel": (status or {}).get("channel", ""),
                    "freq": freq,
                    "band": band_of(freq),
                    "clients": len(cl),
                })
                for mac, c in cl.items():
                    rate = c.get("rate") or {}
                    byts = c.get("bytes") or {}
                    st.clients.append({
                        "mac": mac.lower(),
                        "ap": cfg.name,
                        "ssid": ssid,
                        "network": meta.get("network", ""),
                        "ifname": ifname,
                        "signal": c.get("signal"),
                        "rx_rate": rate.get("rx"),
                        "tx_rate": rate.get("tx"),
                        "rx_bytes": byts.get("rx"),
                        "tx_bytes": byts.get("tx"),
                        "connected": c.get("connected_time"),
                        "freq": freq,
                        "band": band_of(freq),
                        "standard": wifi_standard(c, freq),
                    })
            st.online = True
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        st.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("%s (%s): %s", cfg.name, cfg.host, st.error)
    st.updated = time.time()
    return st


# ---------------------------------------------------------------- router (LAN, bez Wi-Fi)

def parse_host_port(host: str) -> tuple[str, int]:
    if host.count(":") == 1:  # "host:port" (IPv6 bez portu necha byt)
        h, p = host.split(":")
        return h, int(p)
    return host, 22


VLAN_RE = re.compile(r"\.(\d{1,4})$")


def vlan_of(device: str, devstatus: dict | None = None) -> int | None:
    """VLAN ID rozhrani: z nazvu (eth0.30, br-lan.10) nebo z clenu bridge (br-host -> eth0.30). None = netagovane."""
    m = VLAN_RE.search(device or "")
    if m:
        return int(m.group(1))
    members = ((devstatus or {}).get(device) or {}).get("bridge-members") or []
    for mem in members:
        m = VLAN_RE.search(mem)
        if m and not mem.startswith(("phy", "wlan")):
            return int(m.group(1))
    return None


ARL_RE = re.compile(r"Port\s+(\d+)\s*:\s*MAC\s+([0-9a-fA-F:]{17})")
ARL_CMD = ("for s in $(swconfig list 2>/dev/null | sed -n 's/^Found: \\([^ ]*\\).*/\\1/p'); do "
           "echo \"== $s\"; swconfig dev $s get arl_table 2>/dev/null; done")


PORTS_CMD = ("for s in $(swconfig list 2>/dev/null | sed -n 's/^Found: \\([^ ]*\\).*/\\1/p'); do "
             "echo \"== $s\"; swconfig dev $s show 2>/dev/null | grep 'link:'; done")
SW_LINK_RE = re.compile(r"port:(\d+)\s+link:(up|down)(?:\s+speed:(\d+)base\S*)?(?:\s+(full|half)-duplex)?")
PHYS_RE = re.compile(r"^(eth\d+|lan\d+|wan\d*|port\d+|sfp\d*|swp\d+)$")


def parse_swconfig_ports(text: str) -> list[dict]:
    """`swconfig dev X show | grep link:` -> porty switche s rychlosti (swconfig: starsi Atheros/MediaTek)."""
    out = []
    sw = "switch0"
    for line in (text or "").splitlines():
        if line.startswith("== "):
            sw = line[3:].strip()
            continue
        m = SW_LINK_RE.search(line)
        if m:
            up = m.group(2) == "up"
            out.append({"id": f"{sw}:{m.group(1)}", "num": m.group(1), "up": up,
                        "speed": int(m.group(3)) if up and m.group(3) else 0,
                        "duplex": (m.group(4) or "") if up else "", "kind": "switch"})
    return out


def used_devices(devstatus: dict | None, interfaces: list[dict] | None = None) -> set[str]:
    """Fyzicka zarizeni, ktera sit opravdu pouziva: cleny bridge a zarizeni rozhrani (bez VLAN pripony .N)."""
    used = set()
    for d in (devstatus or {}).values():
        if isinstance(d, dict):
            used |= {VLAN_RE.sub("", m) for m in d.get("bridge-members") or []}
    for i in interfaces or []:
        if i.get("device"):
            used.add(VLAN_RE.sub("", i["device"]))
    return used


def parse_dev_ports(devstatus: dict | None, used: set[str] | None = None) -> list[dict]:
    """`ubus call network.device status` -> fyzicke porty (DSA lan1…, wan, eth0…) se `speed` "1000F".
    used: jen zarizeni pouzita v siti (UniFi AP LR ma eth1 do nepouziteho switche)."""
    out = []
    for name, d in (devstatus or {}).items():
        if not PHYS_RE.match(name) or not isinstance(d, dict) or "speed" not in d and "carrier" not in d:
            continue
        if used and name not in used and not name.startswith("wan"):
            continue
        up = bool(d.get("carrier"))
        sp = str(d.get("speed") or "")
        m = re.match(r"^(\d+)([FH]?)$", sp)
        out.append({"id": name, "num": name, "up": up, "speed": int(m.group(1)) if up and m else 0,
                    "duplex": {"F": "full", "H": "half"}.get(m.group(2), "") if up and m else "", "kind": "dev"})
    return sorted(out, key=lambda p: (not p["num"].startswith(("wan",)), p["num"]))


def merge_ports(sw: list[dict], dev: list[dict], switch_vlans: list[str] | None = None) -> list[dict]:
    """Se swconfig switchem jsou porty switche; eth0 je jen spoj do CPU. Bez switche (DSA, router se 2 NIC) porty zarizeni.
    Jen porty pouzite v `switch_vlan`: cip ma casto vic portu, nez je na krabici (UniFi AP LR: 5 portu, venku 1;
    AR8327: port 6 = druhy CPU port). Bez `switch_vlan` se switch nepouziva -> porty zarizeni (eth0)."""
    used = {m.group(1) for ports in (switch_vlans or []) for m in (re.match(r"^(\d+)", p) for p in ports.split()) if m}
    sw = [p for p in sw if p["num"] in used]
    return sw if sw else dev


def parse_arl(text: str) -> dict[str, list[str]]:
    """Vystup `swconfig dev X get arl_table` (pro vsechny switche) -> {"switch0:2": [mac, ...]}."""
    out: dict[str, list[str]] = {}
    sw = "switch0"
    for line in (text or "").splitlines():
        if line.startswith("== "):
            sw = line[3:].strip()
            continue
        m = ARL_RE.search(line)
        if m:
            out.setdefault(f"{sw}:{m.group(1)}", []).append(m.group(2).lower())
    return out


def parse_arp(text: str) -> dict[str, str]:
    """/proc/net/arp -> {ip: mac} (ne podle MAC: router ma stejnou MAC v LAN i ve VLAN)."""
    return {ip: mac for ip, (mac, _) in parse_arp_full(text).items()}


def parse_arp_full(text: str) -> dict[str, tuple[str, str]]:
    """/proc/net/arp -> {ip: (mac, zarizeni)}; jen kompletni zaznamy."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        p = line.split()
        if len(p) >= 4 and p[3] != "00:00:00:00:00:00" and p[2] != "0x0":
            out[p[0]] = (p[3].lower(), p[5] if len(p) >= 6 else "")
    return out


# rozhrani bez L2 (tunely, VPN) – na mape ani v ARP nemaji co delat
NON_L2_PROTOS = {"wireguard", "openvpn", "pppoe", "pppoa", "ppp", "3g", "qmi", "ncm", "mbim", "l2tp", "pptp",
                 "gre", "gretap", "ipip", "6in4", "6to4", "6rd", "dslite", "map", "vti", "xfrm", "zerotier", "tailscale"}


def is_l2_iface(i: dict) -> bool:
    """LAN/VLAN rozhrani: ne WAN, ne tunel/VPN (wg0 apod.)."""
    if i.get("wan"):
        return False
    name = (i.get("name") or "").lower()
    dev = (i.get("device") or "").lower()
    return (i.get("proto") or "") not in NON_L2_PROTOS and not name.startswith(("wg", "tun", "tap", "vpn")) \
        and not dev.startswith(("wg", "tun", "tap", "pppoe-", "ppp", "lo"))


def parse_static_hosts(text: str) -> dict[str, dict]:
    """`uci show dhcp` -> {mac: {name, ip}} ze sekci host (mac muze byt seznam / mezerami oddeleny)."""
    out = {}
    for sec in parse_uci_show(text or "").values():
        if sec.get(".type") != "host":
            continue
        macs = []
        for m in _as_list(sec.get("mac")):
            macs += str(m).split()
        for m in macs:
            if len(m) == 17:
                out[m.lower()] = {"name": str(sec.get("name") or ""), "ip": str(sec.get("ip") or "")}
    return out


def switch_vlan_ports(text: str) -> list[str]:
    return [str(sec.get("ports", "")) for sec in parse_uci_show(text or "").values()
            if sec.get(".type") == "switch_vlan" and sec.get("ports")]


def uci_vlans(text: str) -> dict[str, int]:
    """`uci show network` -> {rozhrani: VLAN ID}. Funguje i se starsim OpenWrt (ifname='eth0.30', type=bridge)
    a s device sekcemi (br_host: name='br-host', ports='eth0.30')."""
    secs = parse_uci_show(text or "")
    devs = {}
    for sec in secs.values():
        if sec.get(".type") == "device" and sec.get("name"):
            devs[sec["name"]] = _as_list(sec.get("ports")) + _as_list(sec.get("ifname"))
    out = {}
    for name, sec in secs.items():
        if sec.get(".type") != "interface":
            continue
        cands = _as_list(sec.get("device")) + _as_list(sec.get("ifname"))
        for c in list(cands):
            cands += devs.get(c, [])
        for c in cands:
            m = VLAN_RE.search(str(c))
            if m and not str(c).startswith(("phy", "wlan")):
                out[name] = int(m.group(1))
                break
    return out


def parse_interfaces(dump, devstatus: dict | None = None, ucivlans: dict | None = None,
                     gw_is_wan: bool = True) -> list[dict]:
    """`ubus call network.interface dump` -> seznam rozhrani (lan, wan, vlany...).
    gw_is_wan: rozhrani s vychozi branou = WAN (router). Na AP False: tam maji brany i VLAN rozhrani
    (dhcp, static s gateway), WAN je jen rozhrani pojmenovane wan*."""
    out = []
    for i in (dump or {}).get("interface") or []:
        name = i.get("interface", "")
        if not name or name == "loopback":
            continue
        v4 = [f"{a.get('address')}/{a.get('mask')}" for a in i.get("ipv4-address") or [] if a.get("address")]
        v6 = [f"{a.get('address')}/{a.get('mask')}" for a in i.get("ipv6-address") or [] if a.get("address")]
        gw = next((r.get("nexthop") for r in i.get("route") or []
                   if r.get("target") == "0.0.0.0" and r.get("mask") == 0 and r.get("nexthop")), "")
        out.append({
            "name": name,
            "up": bool(i.get("up")),
            "proto": i.get("proto", ""),
            "device": i.get("l3_device") or i.get("device", ""),
            "ipv4": ", ".join(v4),
            "ipv6": ", ".join(v6),
            "gateway": gw,
            "dns": ", ".join(i.get("dns-server") or []),
            "uptime": i.get("uptime"),
            "wan": name.lower().startswith("wan") or (gw_is_wan and bool(gw)),
            "vlan": vlan_of(i.get("l3_device") or i.get("device", ""), devstatus) or (ucivlans or {}).get(name),
        })
    return sorted(out, key=lambda x: (not x["wan"], x["name"]))


async def _read_leases(conn, timeout: float) -> dict[str, dict]:
    """MAC -> {ip, hostname}. Zkusi luci-rpc (dnsmasq i odhcpd), pak /tmp/dhcp.leases."""
    leases: dict[str, dict] = {}
    data = await _ubus(conn, "luci-rpc", "getDHCPLeases", timeout)
    if data:
        for l in data.get("dhcp_leases") or []:
            mac = (l.get("macaddr") or "").lower()
            if mac:
                leases[mac] = {"ip": l.get("ipaddr", ""), "hostname": l.get("hostname") or ""}
    if not leases:
        out = await _run(conn, "cat /tmp/dhcp.leases 2>/dev/null", timeout)
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 4:
                leases[p[1].lower()] = {"ip": p[2], "hostname": "" if p[3] == "*" else p[3]}
    return leases


WAKE_MAX = 64


def wake_cmd(ips: list[str]) -> str:
    """Jeden ping na kazdou IP (paralelne, max ~1 s). Tiche zarizeni odpovi a switch AP se znovu nauci,
    na kterem portu je – pri dalsim nacteni ho add-on najde."""
    ips = [ip for ip in ips if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip)][:WAKE_MAX]
    if not ips:
        return ""
    return "for ip in " + " ".join(ips) + "; do ping -c1 -W1 $ip >/dev/null 2>&1 & done; wait"


async def collect_router(cfg: APConfig, key: asyncssh.SSHKey, timeout: float = 10.0,
                         wake: list[str] | None = None) -> tuple[APState, dict[str, dict]]:
    """Router: system jako u AP + rozhrani + DHCP leases, jedno SSH spojeni.
    wake: IP kabelovych zarizeni s neznamym mistem – router je pingne (viz wake_cmd)."""
    st = APState(name=cfg.name, host=cfg.host, kind="router")
    leases: dict[str, dict] = {}
    try:
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[key],
                                    known_hosts=None, connect_timeout=timeout) as conn:
            _, dump, devstatus, ucinet, leases, ucidhcp, arp, swports = await asyncio.gather(
                _read_system(conn, st, timeout),
                _ubus(conn, "network.interface", "dump", timeout),
                _ubus(conn, "network.device", "status", timeout),
                _run(conn, "uci -q show network", timeout),
                _read_leases(conn, timeout),
                _run(conn, "uci -q show dhcp", timeout),
                _run(conn, "cat /proc/net/arp", timeout),
                _run(conn, PORTS_CMD, timeout),
            )
            st.static_hosts = parse_static_hosts(ucidhcp)
            st.interfaces = parse_interfaces(dump, devstatus, uci_vlans(ucinet))
            st.switch_vlans = switch_vlan_ports(ucinet)
            lan_dev = next((i["device"] for i in st.interfaces if i["name"] == "lan"), "br-lan")
            st.mac = (((devstatus or {}).get(lan_dev) or {}).get("macaddr") or "").lower()
            st.ports = merge_ports(parse_swconfig_ports(swports), parse_dev_ports(devstatus, used_devices(devstatus, st.interfaces)),
                                   st.switch_vlans)
            # ARP jen z LAN/VLAN: brana poskytovatele na WAN ani VPN peery nejsou klienti
            skip = {i["device"] for i in st.interfaces if i.get("device") and not is_l2_iface(i)}
            full = parse_arp_full(arp)
            st.arp = {ip: m for ip, (m, d) in full.items() if d not in skip}
            st.arp_dev = {ip: d for ip, (m, d) in full.items() if d not in skip}
            cmd = wake_cmd(wake or [])
            if cmd:
                await _run(conn, cmd, timeout)
            st.online = True
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        st.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("%s (%s): %s", cfg.name, cfg.host, st.error)
    st.updated = time.time()
    return st, leases
