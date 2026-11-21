"""Sber dat z OpenWrt AP pres SSH (ubus)."""
from __future__ import annotations

import asyncio
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
            board, info, wstatus, df_out, hostapd_list, uci_wl, pkg = await asyncio.gather(
                _ubus(conn, "system", "board", timeout),
                _ubus(conn, "system", "info", timeout),
                _ubus(conn, "network.wireless", "status", timeout),
                _run(conn, "df -k /overlay 2>/dev/null | tail -n 1", timeout),
                _run(conn, "ubus list 'hostapd.*'", timeout),
                _run(conn, "uci -q show wireless", timeout),
                _run(conn, "command -v apk >/dev/null && echo apk || (command -v opkg >/dev/null && echo opkg)", timeout),
            )
            st.pkg = pkg.strip()
            st.wifi_ifaces = parse_wifi_ifaces(uci_wl)

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
                status, clients = await asyncio.gather(
                    _ubus(conn, obj, "get_status", timeout),
                    _ubus(conn, obj, "get_clients", timeout),
                )
                meta = iface_meta.get(ifname, {})
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


# ---------------------------------------------------------------- DHCP leases z routeru

async def collect_leases(host: str, user: str, key: asyncssh.SSHKey, timeout: float = 10.0) -> tuple[dict[str, dict], str | None]:
    """MAC -> {ip, hostname}. Zkusi luci-rpc (dnsmasq i odhcpd), pak /tmp/dhcp.leases."""
    leases: dict[str, dict] = {}
    port = 22
    if host.count(":") == 1:  # "host:port"
        host, p = host.split(":")
        port = int(p)
    try:
        async with asyncssh.connect(host, port=port, username=user, client_keys=[key],
                                    known_hosts=None, connect_timeout=timeout) as conn:
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
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        log.warning("DHCP leases z %s: %s", host, e)
        return leases, (f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
    return leases, None
