"""Sber dat z OpenWrt AP pres SSH (ubus). Pouze cteni."""
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
            board, info, wstatus, df_out, hostapd_list = await asyncio.gather(
                _ubus(conn, "system", "board", timeout),
                _ubus(conn, "system", "info", timeout),
                _ubus(conn, "network.wireless", "status", timeout),
                _run(conn, "df -k /overlay 2>/dev/null | tail -n 1", timeout),
                _run(conn, "ubus list 'hostapd.*'", timeout),
            )

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
                    "band": rc.get("band", rc.get("hwmode", "")),
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
                ssid = (status or {}).get("ssid") or meta.get("ssid", "")
                cl = (clients or {}).get("clients") or {}
                st.ssids.append({
                    "ifname": ifname,
                    "ssid": ssid,
                    "network": meta.get("network", ""),
                    "radio": meta.get("radio", ""),
                    "encryption": meta.get("encryption", ""),
                    "channel": (status or {}).get("channel", ""),
                    "freq": (status or {}).get("freq", (clients or {}).get("freq", "")),
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
                        "ht": bool(c.get("ht")),
                        "vht": bool(c.get("vht")),
                        "he": bool(c.get("he")),
                    })
            st.online = True
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        st.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        log.warning("%s (%s): %s", cfg.name, cfg.host, st.error)
    st.updated = time.time()
    return st


# ---------------------------------------------------------------- DHCP leases z routeru

async def collect_leases(host: str, user: str, key: asyncssh.SSHKey, timeout: float = 10.0) -> dict[str, dict]:
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
    return leases
