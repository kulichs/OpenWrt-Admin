"""OpenWrt-Admin - Home Assistant add-on."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

import asyncssh
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import actions, ha, i18n, maint, dhcp as dhcpmod, dns as dnsmod, firewall as fwmod, safeapply, radios as radiomod, services as svcmod, stats as statsmod, switch as switchmod, topology, wired as wiredmod
from .collector import APConfig, APState, band_of, collect_ap, collect_router, norm_band, parse_host_port
from .demo import demo_router, demo_scan, demo_states, demo_switch
from .snmp import SnmpError

DATA = Path(os.environ.get("OWADMIN_DATA", "/data"))
OPTIONS = DATA / "options.json"
KEYFILE = DATA / "id_ed25519"
SEENFILE = DATA / "wired_seen.json"   # posledni zname misto kabelovych zarizeni (AP + port)
KNOWNFILE = DATA / "known_devices.json"  # MAC, ktere add-on uz videl (upozorneni na nova zarizeni)
UPDATESFILE = DATA / "updates.json"   # posledni kontrola aktualizaci
BACKUPS = DATA / "backups"            # zalohy konfigurace (sysupgrade -b) – soucast zaloh HA
INGRESS_IPS = {"172.30.32.2", "127.0.0.1"}
ALLOW_ALL = os.environ.get("OWADMIN_ALLOW_ALL") == "1"

logging.basicConfig(level=os.environ.get("OWADMIN_LOGLEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("openwrt_admin")
logging.getLogger("asyncssh").setLevel(logging.WARNING)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ---------------------------------------------------------------- konfigurace a klic

DEFAULT_PING = [{"name": "Cloudflare", "host": "1.1.1.1"}, {"name": "Google DNS", "host": "8.8.8.8"}]
TRAFFIC_SOURCES = ("auto", "nlbwmon", "conntrack", "off")

def load_options() -> dict:
    opts = {"aps": [], "ssh_user": "root", "poll_interval": 30, "dhcp_router": "", "router_name": "Router", "demo": False,
            "full_access": [], "read_access": [], "refresh_interval": 30, "language": "auto",
            "ha_names": True, "names": [], "notify_new_devices": True, "notify_service": "", "notify_random_mac": True,
            "update_check_hours": 12, "backup_interval_days": 7, "backup_keep": 10, "switches": [],
            "stats": True, "stats_interval": 5, "ping_targets": list(DEFAULT_PING), "ping_devices": True,
            "traffic_source": "auto", "top_clients": 10, "ha_sensors": False, "ha_sensors_interval": 30,
            "new_device_hours": 24, "radio_scan": False}
    if OPTIONS.exists():
        opts.update(json.loads(OPTIONS.read_text()))
    return opts


def load_key() -> asyncssh.SSHKey:
    if not KEYFILE.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519", comment="ha-openwrt-admin")
        key.write_private_key(str(KEYFILE))
        KEYFILE.chmod(0o600)
        key.write_public_key(str(KEYFILE) + ".pub")
        log.info("Vygenerovan novy SSH klic %s", KEYFILE)
    return asyncssh.read_private_key(str(KEYFILE))


# ---------------------------------------------------------------- stav a poller

def split_devices(opts: dict) -> tuple[list[dict], dict | None]:
    """Jen definovana zarizeni ze seznamu `aps`: type ap (vychozi) / router.
    Zpetna kompatibilita: zastaraly `dhcp_router` bez zarizeni typu router -> zarizeni se stejnou IP
    se stane routerem; kdyz takove neni, vznikne router z `dhcp_router` + `router_name`."""
    items = [dict(i) for i in opts.get("aps") or []]
    router = next((i for i in items if i.get("type") == "router"), None)
    if router is None and opts.get("dhcp_router"):
        host, port = parse_host_port(str(opts["dhcp_router"]))
        router = next((i for i in items if i.get("host") == host), None)
        if router is None:
            router = {"name": str(opts.get("router_name") or "Router"), "host": host, "port": port}
        else:
            log.info("dhcp_router %s = zarizeni %s -> pouzije se jako router (doporuceno: type: router)", host, router["name"])
    extra = [i for i in items if i.get("type") == "router" and i is not router]
    for i in extra:
        log.warning("Vice zarizeni s type: router, %s se bere jako AP", i["name"])
    aps = [i for i in items if i is not router]
    return aps, router


class Store:
    def __init__(self):
        self.opts = load_options()
        self.key = load_key()
        self.pubkey = self.key.export_public_key().decode().strip()
        self.ap_items, self.router_item = split_devices(self.opts)
        self.aps: list[APState] = [APState(name=a["name"], host=a["host"]) for a in self.ap_items]
        # spravovane switche pres SNMP (jen cteni)
        self.switch_items: list[dict] = [dict(x) for x in self.opts.get("switches") or [] if x.get("name") and x.get("host")]
        self.switches: list[APState] = [APState(name=x["name"], host=x["host"], kind="switch") for x in self.switch_items]
        self.leases: dict[str, dict] = {}
        self.router: APState | None = (APState(name=self.router_item["name"], host=self.router_item["host"], kind="router")
                                       if self.router_item else None)
        self.last_poll = 0.0
        self.seen: dict[str, dict] = self._load_seen()
        self.wired_state: dict | None = None
        # jmena a IP zarizeni: {mac: {ip, hostname, src, lease}} – viz build_info()
        self.info: dict[str, dict] = {}
        self.ha_names: dict[str, str] = {}
        self.ha_status = "off"   # off / ok / chybova hlaska
        self.ha_fetched = 0.0
        self.lock = asyncio.Lock()
        self.ap_locks: dict[str, asyncio.Lock] = {}
        self.pending: dict[str, tuple[float, dict[str, list[actions.SsidChange]]]] = {}
        # udrzba
        self.known = maint.KnownDevices(KNOWNFILE) if not self.opts.get("demo") else maint.KnownDevices(Path("/nonexistent"))
        self.updates: dict[str, dict] = self._load_json(UPDATESFILE) if not self.opts.get("demo") else {}
        self.versions: dict = {}        # https://downloads.openwrt.org/.versions.json
        self.versions_ts = 0.0
        self.ha_lang = ""
        self.notify_status = ""         # posledni chyba odeslani upozorneni
        self.backup_busy: set[str] = set()
        self.ban_meta: dict[str, dict] = {}  # mac -> {until, ssid} – blokace zadane z add-onu
        # statistiky (rychlosti, ping, top klienti) – jen v pameti, posledni hodina
        self.stats = statsmod.Stats(self.stats_interval)
        self.stats_conn: asyncssh.SSHClientConnection | None = None   # trvale SSH spojeni k routeru
        self.stats_last = 0.0
        self.demo_feed = statsmod.DemoFeed() if self.opts.get("demo") else None
        self.sensor_status = ""   # posledni vysledek odeslani senzoru do HA
        self.sensor_count = 0
        self._bad_hosts: set[str] = set()
        self.scans: dict[tuple[str, str], dict] = {}   # (ap, radio) -> {ts, cells, error} – sken okoli
        self.pending_cmds: dict[str, tuple[float, str, dict[str, list[str]]]] = {}  # token -> (ts, druh, {ap: prikazy})
        # sluzby, adblock, banIP – nacita se az pri otevreni stranky Sluzby (poller zustava lehky)
        self.svc: dict[str, dict] = {}                 # zarizeni -> vysledek services.parse_all
        self.svc_pending: dict[str, tuple[float, str, list[str], dict]] = {}  # token -> (ts, zarizeni, prikazy, info)
        self.svc_demo: dict[str, dict] = {}            # demo: stav adblocku/banIP po akcich
        self.dhcp: dict | None = None                  # DHCP routeru (dhcp.build) – nacita se se zalozkou DHCP
        self.dhcp_demo: dict = {k: dict(v) for k, v in dhcpmod.DEMO_UCI.items()}
        self.dhcp_demo_wan: list[str] = []            # demo: DNS na WAN
        self.fw: dict | None = None                    # firewall routeru (firewall.build)
        self.fw_demo: dict[str, bool] = {}             # demo: zapnuto/vypnuto pravidel a presmerovani

    @staticmethod
    def _load_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    @property
    def router_name(self) -> str:
        return self.router_item["name"] if self.router_item else ""

    def _cfg(self, item: dict) -> APConfig:
        """SSH uzivatel: u zarizeni `user`, jinak spolecny `ssh_user` (vychozi root)."""
        return APConfig(name=item["name"], host=item["host"], port=int(item.get("port") or 22),
                        user=str(item.get("user") or self.opts.get("ssh_user") or "root"))

    def router_cfg(self) -> APConfig | None:
        return self._cfg(self.router_item) if self.router_item else None

    def ap(self, name: str) -> APState:
        """AP nebo router podle jmena (vse, co ma stranku a akce)."""
        for d in [*self.aps, self.router, *self.switches]:
            if d is not None and d.name == name:
                return d
        raise HTTPException(404, "device not found")

    def cfg(self, name: str) -> APConfig:
        item = next((i for i in [*self.ap_items, self.router_item] if i and i["name"] == name), None)
        if not item:
            raise HTTPException(404, "device not found")
        return self._cfg(item)

    @property
    def router_ok(self) -> bool:
        return bool(self.router and self.router.online)

    @property
    def router_error(self) -> str | None:
        return self.router.error if self.router else None

    def ap_lock(self, name: str) -> asyncio.Lock:
        return self.ap_locks.setdefault(name, asyncio.Lock())

    def ssid_groups(self) -> list[dict]:
        """SSID napric AP: [{ssid, items:[{ap, section, device, band, network, hidden, disabled, ...}]}]."""
        groups: dict[str, list] = {}
        for ap in self.aps:
            bands = {r["name"]: r.get("band", "") for r in ap.radios}
            live = {s["radio"]: s for s in ap.ssids}
            for i in ap.wifi_ifaces:
                if i["mode"] not in ("ap", ""):
                    continue
                band = norm_band(bands.get(i["device"], "")) or \
                    band_of((live.get(i["device"]) or {}).get("freq"))
                groups.setdefault(i["ssid"], []).append({**i, "ap": ap.name, "band": band})
        return [{"ssid": k, "items": v} for k, v in sorted(groups.items(), key=lambda kv: kv[0].lower())]

    async def poll(self):
        async with self.lock:
            if self.opts.get("demo"):
                self.aps, self.leases = demo_states()
                if self.router:
                    self.router = demo_router(self.router.name, self.router.host)
                # ukazka "naposledy videno": vypnuta TV na portu 4 AP-Office
                self.seen = {"02:00:00:00:10:04": {"ap": "AP-Office", "port": "5", "ts": time.time() - 3 * 3600,
                                                   "hostname": "tv-office", "ip": "192.168.1.13"}}
                # ukazka jmen z HA: Wi-Fi klienti bez jmena (ESPHome s pevnou IP)
                nameless = [c["mac"] for a in self.aps for c in a.clients if c["mac"] not in self.leases]
                self.ha_names = {m: f"esp-{n}" for m, n in zip(nameless, ["teplomer-obyvak", "rele-garaz", "plyn"])}
                self.ha_status = "ok"
                if self.router:
                    self.router.arp.update({f"192.168.1.{60 + i}": m for i, m in enumerate(nameless)})
                    # rezervace pro zarizeni, ktera ted nejsou v siti (vypnuta / mimo Wi-Fi)
                    self.router.static_hosts = {"02:00:00:00:20:01": {"name": "tv-bedroom", "ip": "192.168.1.207"},
                                                "02:00:00:00:20:02": {"name": "phone-guest", "ip": "192.168.1.208"},
                                                "02:00:00:00:20:03": {"name": "xbox", "ip": "192.168.1.218"},
                                                "02:00:00:00:30:01": {"name": "mailserver", "ip": "192.168.100.7"}}
                self.info = self.build_info()
                if self.router:
                    self.router.mac = "02:00:00:00:00:01"
                sw_items = self.switch_items or [{"name": "Switch-Core", "host": "192.168.1.5"}]
                self.switches = [demo_switch(x["name"], x["host"], self.aps, "02:00:00:00:00:01") for x in sw_items[:1]]
                self.attach_router_to_switches()
                self.wired_state = wiredmod.locate(self.l2(), self.router, self.info, self.seen, known=self._known())
                self.stats.update_wifi(time.time(), self.aps)
                self.stats.update_radios(time.time(), self.aps)
                for ap in self.aps:  # demo: hodina historie vytizeni kanalu
                    for r in ap.radios:
                        sv = r.get("survey")
                        ser = self.stats.radio_util.setdefault((ap.name, r["name"]), statsmod.Series(360)) if sv else None
                        if ser is not None and len(ser.d) < 2:
                            base = 100 * sv["busy"] / max(1, sv["active"])
                            for k in range(120, 0, -1):
                                ser.add(time.time() - k * 30, max(0.0, base * (0.7 + 0.6 * abs(math.sin(k / 9)))))
                # ukazka: zablokovany klient na HomeWiFi (oba AP)
                for ap in self.aps:
                    ap.bans_ok = ap.online
                    ap.bans = [{"mac": "02:00:00:00:40:01", "ifname": x["ifname"], "ssid": x["ssid"]}
                               for x in ap.ssids if x["ssid"] == "HomeWiFi"] if ap.online else []
                self.ban_meta.setdefault("02:00:00:00:40:01", {"until": time.time() + 2400, "ssid": "HomeWiFi"})
                # ukazka: nove zarizeni (stitek „nove“) a aktualizace
                self.known.data = {"02:00:00:00:30:01": {"first": time.time() - 2 * 3600, "last": time.time()}}
                self.versions = {"stable_version": "25.12.3", "oldstable_version": "24.10.6"}
                self.updates = {a.name: {"ts": time.time() - 3 * 3600, "error": "", "pkgs": pk} for a, pk in zip(
                    [*([self.router] if self.router else []), *self.aps],
                    [[{"name": "dnsmasq-full", "old": "2.91-r1", "new": "2.91-r2"}],
                     [{"name": "hostapd-utils", "old": "2025.08.26~1", "new": "2025.08.26~2"},
                      {"name": "libubox20251010", "old": "2025.10.10~1", "new": "2025.10.10~2"}], [], []])}
                self.last_poll = time.time()
                return
            user = self.opts.get("ssh_user", "root")
            cfgs = [self._cfg(a) for a in self.ap_items]
            tasks = [collect_ap(c, self.key) for c in cfgs]
            rc = self.router_cfg()
            wake = self.wake_ips()
            n_sw = len(self.switch_items)
            tasks += [switchmod.collect_switch(x["name"], x["host"], str(x.get("community") or "public"),
                                               int(x.get("port") or 161)) for x in self.switch_items]
            if rc:
                tasks.append(collect_router(rc, self.key, wake=wake))
            res = await asyncio.gather(*tasks)
            if rc:
                self.router, leases = res.pop()
                self.leases = leases or self.leases
            self.switches = list(res[len(res) - n_sw:]) if n_sw else []
            self.aps = list(res[:len(res) - n_sw]) if n_sw else list(res)
            self.attach_router_to_switches()
            await self.refresh_ha()
            self.info = self.build_info()
            self.wired_state = wiredmod.locate(self.l2(), self.router, self.info, self.seen, known=self._known())
            self.stats.update_wifi(time.time(), self.aps)
            self.stats.update_radios(time.time(), self.aps)
            self._save_seen()
            self.last_poll = time.time()
        await self.track_new()

    # ------------------------------------------------------------ statistiky
    @property
    def stats_interval(self) -> int:
        try:
            return min(60, max(2, int(self.opts.get("stats_interval", 5) or 5)))
        except (TypeError, ValueError):
            return 5

    @property
    def stats_on(self) -> bool:
        return bool(self.opts.get("stats", True))

    @property
    def traffic_source(self) -> str:
        v = str(self.opts.get("traffic_source") or "auto")
        return v if v in TRAFFIC_SOURCES else "auto"

    def ping_plan(self) -> list[tuple[str, str, str, str]]:
        """[(klic, jmeno, adresa, druh)] – co pinga router: cile z `ping_targets` (internet), a s `ping_devices`
        i brana poskytovatele (WAN gateway), AP a switche. Jen zadane nebo zname adresy, zadne skenovani."""
        out, keys = [], set()

        def add(key, name, host, kind):
            host = str(host or "").strip()
            if not statsmod.valid_host(host):
                if host and host not in self._bad_hosts:
                    self._bad_hosts.add(host)
                    log.warning("Ping: neplatna adresa %r (%s) se preskoci", host, name)
                return
            k, n = key, 2
            while k in keys:
                k, n = f"{key}_{n}", n + 1
            keys.add(k)
            out.append((k, str(name or host), host, kind))
        for x in self.opts.get("ping_targets") or []:
            if isinstance(x, dict):
                add("net:" + statsmod.slug(str(x.get("name") or x.get("host") or "")), x.get("name"), x.get("host"), "internet")
        if self.opts.get("ping_devices", True):
            wan = next((i for i in (self.router.interfaces if self.router else []) if i.get("wan") and i.get("gateway")), None)
            if wan:
                add("gw", "WAN gateway", wan["gateway"], "gateway")
            for d in [*self.switches, *self.aps]:
                add("dev:" + statsmod.slug(d.name), d.name, d.host.split(":")[0], "device")
        return out

    def internet_keys(self) -> list[str]:
        return [k for k, p in self.stats.pings.items() if p.kind == "internet"]

    def is_local_fn(self):
        resolve = topology.net_resolver(self.router)
        return lambda ip: resolve(ip) is not None

    def resolve_host(self, mac: str, ip: str) -> dict:
        """Jmeno a IP klienta podle MAC (nlbwmon) nebo IP (conntrack)."""
        if mac and mac in self.info:
            e = self.info[mac]
            return {"hostname": e.get("hostname", ""), "ip": e.get("ip", "") or ip, "mac": mac}
        if ip:
            for m, e in self.info.items():
                if e.get("ip") == ip:
                    return {"hostname": e.get("hostname", ""), "ip": ip, "mac": m}
        return {"hostname": "", "ip": ip, "mac": mac}

    async def _stats_conn(self, rc: APConfig) -> asyncssh.SSHClientConnection:
        if self.stats_conn is None:
            self.stats_conn = await asyncssh.connect(rc.host, port=rc.port, username=rc.user, client_keys=[self.key],
                                                     known_hosts=None, connect_timeout=10, keepalive_interval=30)
        return self.stats_conn

    def _stats_close(self):
        if self.stats_conn is not None:
            try:
                self.stats_conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.stats_conn = None

    async def _stats_router(self, now: float, plan: list[tuple]):
        rc = self.router_cfg()
        if not rc:
            return
        rp = self.stats.ping_target("router", rc.name, rc.host, "router")
        cmd = statsmod.router_cmd([p[2] for p in plan], self.traffic_source)
        try:
            conn = await self._stats_conn(rc)
            t0 = time.monotonic()
            await asyncio.wait_for(conn.run("true", check=False), 5)
            rp.add(now, round((time.monotonic() - t0) * 1000, 1))
            res = await asyncio.wait_for(conn.run(cmd, check=False), 20)
            out = res.stdout if isinstance(res.stdout, str) else (res.stdout or b"").decode(errors="replace")
            self.stats.feed_router(now, out, [p[0] for p in plan], self.is_local_fn())
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
            rp.add(now, None)
            self.stats.router_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            log.debug("Statistiky z routeru: %s", self.stats.router_error)
            self._stats_close()

    async def _stats_switch(self, now: float, item: dict, sw: APState):
        try:
            c = await switchmod.read_counters(item["host"], str(item.get("community") or "public"), int(item.get("port") or 161))
            self.stats.feed_switch(now, sw.name, c, sw.ports)
        except (OSError, SnmpError) as e:
            self.stats.switch_errors[sw.name] = f"{type(e).__name__}: {e}"

    def _demo_clients(self) -> list[tuple[str, str]]:
        ap_macs = {a.mac for a in self.l2() if a.mac}
        return [(m, e["ip"]) for m, e in self.info.items() if e.get("ip") and m not in ap_macs][:30]

    def _demo_tick(self, ts: float, dt: float, plan: list[tuple]):
        text = self.demo_feed.router_text(ts, [p[2] for p in plan], dt, self._demo_clients())
        self.stats.feed_router(ts, text, [p[0] for p in plan], self.is_local_fn())
        if self.router:
            self.stats.ping_target("router", self.router.name, self.router.host, "router").add(ts, round(1.5 + (ts % 7) / 10, 1))
        for sw in self.switches:
            self.stats.feed_switch(ts, sw.name, self.demo_feed.switch_counters(ts, sw.name, sw.ports, dt), sw.ports)

    async def stats_tick(self):
        if not self.stats_on:
            return
        now = time.time()
        plan = self.ping_plan()
        for k, name, host, kind in plan:
            self.stats.ping_target(k, name, host, kind)
        self.stats.prune_pings({p[0] for p in plan} | ({"router"} if self.router_item else set()))
        if self.opts.get("demo"):
            if not self.last_poll:
                return
            step = self.stats_interval
            if not self.stats_last:  # demo: rovnou hodina historie (grafy, screenshoty)
                for k in range(statsmod.HISTORY // step, 0, -1):
                    self._demo_tick(now - k * step, step, plan)
            else:
                self._demo_tick(now, now - self.stats_last, plan)
            self.stats_last = now
            return
        tasks = [self._stats_router(now, plan)]
        for item in self.switch_items:
            sw = next((x for x in self.switches if x.name == item["name"]), None)
            if sw and sw.online and sw.ports:
                tasks.append(self._stats_switch(now, item, sw))
        await asyncio.gather(*tasks)
        self.stats_last = now

    def router_ifaces(self) -> list[dict]:
        """Rozhrani routeru se statistikami: WAN, LAN, VLANy, pak ostatni (tunely); bez loopbacku.
        Vic rozhrani na jednom zarizeni (wan + wan6 na eth0) = jeden radek, prednost ma zapnute."""
        if not self.router:
            return []
        cand = sorted((i for i in self.router.interfaces if i.get("device") in self.stats.ifaces),
                      key=lambda i: (not i.get("up"), "6" in i["name"], i["name"]))
        out, seen = [], set()
        for i in cand:
            if i["device"] not in seen:
                seen.add(i["device"])
                out.append(i)
        return sorted(out, key=lambda i: (not i.get("wan"), i["name"] != "lan", i.get("vlan") is None, i.get("vlan") or 0, i["name"]))

    def wan_load(self) -> tuple | None:
        wan = next((i for i in self.router_ifaces() if i.get("wan") and i.get("up")), None)
        return self.stats.iface_rate(wan["device"]) if wan else None

    def link_loads(self) -> dict[str, tuple]:
        """Provoz na privodu kazdeho AP/switche (z citacu portu switche): {jmeno: (k zarizeni, od zarizeni)}."""
        out: dict[str, tuple] = {}
        if self.wan_load():
            out["__wan__"] = self.wan_load()
        w = self.wired_state or {}
        parents = w.get("parent") or {}
        by_name = {x.name: x for x in self.l2()}
        for a in self.l2():
            par = by_name.get(parents.get(a.name, ""))
            if par is not None and par.kind == "switch" and a.mac:
                port = wiredmod.analyze(par, self.router)["local"].get(a.mac)
                if port:
                    d, u = self.stats.port_rate(par.name, port.split(":")[1])
                    if d is not None:
                        out[a.name] = (d, u)
            elif a.kind == "switch" and a.online:
                up = wiredmod.uplink_port(a, self.router)
                if up:
                    d, u = self.stats.port_rate(a.name, up["num"])
                    if d is not None:  # uplink: in do switche = smerem dolu od routeru
                        out[a.name] = (u, d)
        return out

    def stats_view(self) -> dict:
        st = self.stats
        ifaces = []
        for i in self.router_ifaces():
            e = st.ifaces[i["device"]]
            ifaces.append({**i, "rx": e["rx"].last, "tx": e["tx"].last, "pps": e["pps"].last,
                           "peak_rx": e["rx"].peak(), "peak_tx": e["tx"].peak(),
                           "spark": statsmod.spark([e["rx"].values(), e["tx"].values()], 220, 34)})
        wan = next((x for x in ifaces if x.get("wan")), None)
        groups = {"internet": [], "gateway": [], "router": [], "device": []}
        for p in st.pings.values():
            groups.setdefault(p.kind, []).append(p)
        inet = groups["internet"]
        top = st.top_clients(int(self.opts.get("top_clients", 10) or 10), self.resolve_host)
        tmax = max((r["down"] for r in top), default=0) or 1
        wifi = []
        for c in self.all_clients():
            r = st.wifi.get((c["ap"], c["mac"]))
            if r and r[0] + r[1] > 0:
                wifi.append({**c, "down": r[0], "up": r[1]})
        wifi.sort(key=lambda c: -(c["down"] + c["up"]))
        port_devs = {}
        for sw in self.switches:
            for p in self.port_table(sw):
                port_devs[(sw.name, p["num"])] = {"alias": p.get("alias", ""), "speed": p.get("speed", 0),
                                                  "devices": [d.get("hostname") or d.get("mac") for d in p["devices"]]}
        ports = []
        for r in st.busiest_ports(12):
            e = r.pop("series")
            ports.append({**r, **port_devs.get((r["switch"], r["num"]), {}),
                          "spark": statsmod.spark([e["down"].values(), e["up"].values()], 160, 28)})
        return {"ifaces": ifaces, "wan": wan, "temps": st.temps, "temp": st.temp.last,
                "temp_spark": statsmod.spark([st.temp.values()], 120, 28, vmax=max(80.0, st.temp.peak()), area=False),
                "load": st.load, "pings": groups, "inet_ok": any(p.ok for p in inet) if inet else None,
                "inet_ms": min((p.last for p in inet if p.last is not None), default=None),
                "top": top, "top_max": tmax, "source": st.traffic.source, "wifi": wifi[:10],
                "ports": ports, "router_error": st.router_error, "switch_errors": st.switch_errors,
                "updated": st.router_ts, "interval": self.stats_interval}

    async def publish_sensors(self):
        top = self.stats.top_clients(int(self.opts.get("top_clients", 10) or 10), self.resolve_host)
        ents = statsmod.ha_entities(self.stats, self.router.name if self.router else "router", self.router_ifaces(), top,
                                    self.switches, self.internet_keys(), radiomod.radio_rows(self.aps, self.stats.util()))
        self.sensor_count = len(ents)
        if self.opts.get("demo") or not os.environ.get("SUPERVISOR_TOKEN"):
            self.sensor_status = "demo"
            return
        try:
            await asyncio.to_thread(ha.set_states, ents)
            self.sensor_status = "ok"
        except Exception as e:  # noqa: BLE001 - senzory jsou bonus
            self.sensor_status = f"{type(e).__name__}: {e}"
            log.warning("Senzory do HA: %s", self.sensor_status)

    # ------------------------------------------------------------ radia
    def own_ids(self) -> tuple[set[str], set[str]]:
        """BSSID a SSID vlastnich AP (pro odliseni od sousedu ve skenu)."""
        bss = {b for a in self.aps for r in a.radios for b in r.get("bssids") or []}
        ssids = {x["ssid"] for a in self.aps for x in a.ssids if x.get("ssid")}
        return bss, ssids

    def mark_own(self, cells: list[dict]) -> list[dict]:
        bss, ssids = self.own_ids()
        names = {b: a.name for a in self.aps for r in a.radios for b in r.get("bssids") or []}
        for c in cells:
            c["own"] = c["bssid"] in bss or (bool(c["essid"]) and c["essid"] in ssids)
            c["own_ap"] = names.get(c["bssid"], "")
        return cells

    async def scan_radio(self, ap: APState, radio: dict) -> dict:
        ifn = next(iter(radio.get("ifnames") or []), "")
        key = (ap.name, radio["name"])
        if self.opts.get("demo"):
            await asyncio.sleep(1)
            res = {"ts": time.time(), "cells": self.mark_own(demo_scan(radio)), "error": ""}
        else:
            try:
                cmd = radiomod.scan_cmd(ifn)
            except ValueError:
                res = {"ts": time.time(), "cells": [], "error": "no interface"}
            else:
                async with self.ap_lock(ap.name):
                    r = await actions.run(self.cfg(ap.name), self.key, cmd, timeout=40)
                cells = radiomod.parse_scan(r.output) if r.ok else []
                res = {"ts": time.time(), "cells": self.mark_own(cells),
                       "error": "" if cells else (r.output.strip().splitlines() or ["?"])[-1][:200]}
        self.scans[key] = res
        log.info("Sken %s %s: %d siti%s", ap.name, radio["name"], len(res["cells"]),
                 f" ({res['error']})" if res["error"] else "")
        return res

    def radio(self, ap_name: str, radio_name: str) -> tuple[APState, dict] | None:
        a = next((x for x in self.aps if x.name == ap_name and x.online), None)
        r = next((x for x in (a.radios if a else []) if x["name"] == radio_name), None)
        return (a, r) if r else None

    def scan_cells(self) -> list[dict]:
        """Vysledky vsech skenu dohromady (kazda cizi sit jednou, nejsilnejsi signal)."""
        merged: dict[str, dict] = {}
        for sc in self.scans.values():
            for c in sc.get("cells") or []:
                if c["bssid"] not in merged or (c["signal"] or -200) > (merged[c["bssid"]]["signal"] or -200):
                    merged[c["bssid"]] = c
        return list(merged.values())

    def add_pending(self, kind: str, plan: dict[str, list[str]]) -> str:
        now = time.time()
        self.pending_cmds = {k: v for k, v in self.pending_cmds.items() if now - v[0] < 600}
        token = secrets.token_urlsafe(16)
        self.pending_cmds[token] = (now, kind, plan)
        return token

    def svc_devices(self) -> list[APState]:
        """Zarizeni s OpenWrt (router + AP), switche ne."""
        return [d for d in [self.router, *self.aps] if d is not None and d.kind != "switch"]

    async def load_services(self, only: str | None = None, max_age: float = 15.0, force: bool = False):
        """Nacte sluzby zarizeni (only = jen jedno). Jen cteni – do syslogu zarizeni se nic nezapisuje."""
        now = time.time()

        async def one(d: APState):
            if only and d.name != only:
                return
            if not force and now - (self.svc.get(d.name) or {}).get("ts", 0) < max_age:
                return
            if self.opts.get("demo"):
                text = svcmod.demo_text(d.name, d.kind == "router", self.svc_demo.get(d.name))
                self.svc[d.name] = svcmod.parse_all(text)
                return
            if not d.online:
                self.svc[d.name] = {"ts": now, "services": [], "adblock": None, "banip": None, "error": d.error or "offline"}
                return
            r = await actions.run(self.cfg(d.name), self.key, svcmod.SVC_CMD, timeout=30, audit=False)
            res = svcmod.parse_all(r.output)
            if res["error"] and not r.ok:
                res["error"] = r.output[-300:]
            self.svc[d.name] = res
        await asyncio.gather(*(one(d) for d in self.svc_devices()))

    async def load_dhcp(self, max_age: float = 60.0, force: bool = False):
        """DHCP routeru (jen cteni, bez zapisu do logu routeru)."""
        r = self.router
        if not r or (not force and self.dhcp and time.time() - self.dhcp["ts"] < max_age):
            return
        if self.opts.get("demo"):
            self.dhcp = dhcpmod.build(dhcpmod.demo_text(self.dhcp_demo, self.leases, self.dhcp_demo_wan))
            return
        if not r.online:
            self.dhcp = {"ts": time.time(), "error": r.error or "offline"}
            return
        res = await actions.run(self.cfg(r.name), self.key, dhcpmod.READ_CMD, timeout=30, audit=False)
        m = dhcpmod.build(res.output)
        if m["error"] and not res.ok:
            m["error"] = res.output[-300:]
        self.dhcp = m

    async def load_fw(self, max_age: float = 60.0, force: bool = False):
        """Firewall routeru – jen cteni (bez zapisu do logu routeru)."""
        r = self.router
        if not r or (not force and self.fw and time.time() - self.fw["ts"] < max_age):
            return
        if self.opts.get("demo"):
            m = fwmod.build(fwmod.DEMO)
            for it in m["rules"] + m["redirects"]:
                if it["id"] in self.fw_demo:
                    it["enabled"] = self.fw_demo[it["id"]]
            self.fw = m
            return
        if not r.online:
            self.fw = {"ts": time.time(), "error": r.error or "offline"}
            return
        res = await actions.run(self.cfg(r.name), self.key, fwmod.READ_CMD, timeout=30, audit=False)
        m = fwmod.build(res.output)
        if m["error"] and not res.ok:
            m["error"] = res.output[-300:]
        self.fw = m

    def arp_ips(self) -> set[str]:
        """IP, ktere router vidi v ARP (i zarizeni s pevnou IP mimo DHCP) – navrh volne IP je vynecha."""
        return set((self.router.arp or {}).keys()) if self.router else set()

    def add_svc_pending(self, ap: str, cmds: list[str], info: dict) -> str:
        now = time.time()
        self.svc_pending = {k: v for k, v in self.svc_pending.items() if now - v[0] < 600}
        token = secrets.token_urlsafe(16)
        self.svc_pending[token] = (now, ap, cmds, info)
        return token

    def demo_svc_apply(self, ap: str, info: dict):
        """Demo: zmena stavu po akci, aby stranka ukazala vysledek."""
        st = self.svc_demo.setdefault(ap, {})
        cur = self.svc.get(ap) or {}
        adb, ban = cur.get("adblock") or {}, cur.get("banip") or {}
        st.setdefault("adb_allow", list(adb.get("allow", []))); st.setdefault("adb_block", list(adb.get("block", [])))
        st.setdefault("ban_allow", list(ban.get("allow", []))); st.setdefault("ban_block", list(ban.get("block", [])))
        op = info.get("op")
        if op == "adb_suspend":
            m = int(info.get("minutes") or 0)
            st["adb_paused_until"] = time.time() + m * 60 if m else -1
        elif op in ("adb_resume", "adb_reload"):
            st["adb_paused_until"] = 0
        elif op in ("adb_list", "ban_list"):
            pre = "adb" if op == "adb_list" else "ban"
            lst, other = st[f"{pre}_{info['target']}"], st[f"{pre}_{'block' if info['target'] == 'allow' else 'allow'}"]
            if info["action"] == "add":
                if info["value"] not in lst:
                    lst.append(info["value"])
                if info["value"] in other:
                    other.remove(info["value"])
            elif info["value"] in lst:
                lst.remove(info["value"])

    def radios_edit(self, plan: list[dict] | None) -> list[dict]:
        """Radky formulare pro upravu radii: moznosti z iwinfo a predvyplnena hodnota (navrh nebo nastaveni)."""
        pl = {(p["ap"], p["radio"]): p for p in plan or []}
        out = []
        for a in self.aps:
            if not a.online:
                continue
            for r in a.radios:
                p = pl.get((a.name, r["name"]))
                cfg_ch, cfg_ht, cfg_tx = str(r.get("channel", "")), str(r.get("htmode") or ""), str(r.get("txpower") or "auto")
                out.append({"ap": a.name, "radio": r["name"], "band": r.get("band", ""), "up": r.get("up"),
                            "channels": radiomod.channel_options(r), "htmodes": radiomod.htmode_options(r),
                            "txpowers": radiomod.txpower_options(r), "cfg_channel": cfg_ch, "cfg_htmode": cfg_ht,
                            "cfg_txpower": cfg_tx,
                            # z navrhu jen to, co navrh opravdu meni (kanal i pri "auto" – navrh = pevny kanal)
                            "sel_channel": str(p["new"]["channel"]) if p and (p["new"]["channel"] != p["cur"]["channel"]
                                                                              or cfg_ch in ("auto", "0")) else cfg_ch,
                            "sel_htmode": p["new"]["htmode"] if p and p["new"]["htmode"] != p["cur"]["htmode"] else cfg_ht,
                            "sel_txpower": str(p["new"]["txpower"]) if p and p["new"]["txpower"] is not None
                            and p["new"]["txpower"] != p["cur"]["txpower"] else cfg_tx,
                            "why": p["why"] if p else [], "planned": bool(p and p["changed"])})
        return out

    def radios_view(self) -> dict:
        rows = radiomod.radio_rows(self.aps, self.stats.util())
        for r in rows:
            ser = self.stats.radio_util.get((r["ap"], r["radio"]))
            r["util_avg"] = ser.avg() if ser else None
            r["spark"] = statsmod.spark([ser.values()], 140, 26, vmax=100) if ser and len(ser.d) > 1 else None
            r["scan"] = self.scans.get((r["ap"], r["radio"]))
        bands = sorted({r["band"] for r in rows if r["band"]}, key=lambda b: (b[:1] != "2", b))
        cells = self.scan_cells()
        return {"rows": rows, "warnings": radiomod.warnings(rows) + radiomod.scan_warnings(rows, self.scans),
                "maps": [radiomod.channel_map(rows, b, cells) for b in bands],
                "offline": [a.name for a in self.aps if not a.online],
                "scan_on": bool(self.opts.get("radio_scan") or self.opts.get("demo")), "scanned": bool(self.scans)}

    # ------------------------------------------------------------ switche
    def l2(self) -> list[APState]:
        """Zarizeni s tabulkou MAC adres pro umisteni kabelovych zarizeni: switche a AP."""
        return [*self.switches, *self.aps]

    def router_mac(self) -> str:
        if self.router and self.router.mac:
            return self.router.mac
        ip = self.router.host.split(":")[0] if self.router else ""
        return next((a.arp[ip] for a in self.aps if ip and ip in a.arp), "")

    def attach_router_to_switches(self):
        """Switch nema ARP: uplink pozna podle MAC routeru (z routeru nebo z ARP tabulky AP)."""
        mac = self.router_mac()
        ip = self.router.host.split(":")[0] if self.router else ""
        for sw in self.switches:
            sw.arp = {ip: mac} if ip and mac else {}

    # ------------------------------------------------------------ nova zarizeni
    def _t(self):
        lang = self.opts.get("language") if self.opts.get("language") in i18n.LANGUAGES else (i18n.normalize(self.ha_lang) or "en")
        return i18n.translator(lang)

    def where(self, l: dict, t) -> str:
        if l.get("ap"):
            return t("notify.where_wifi", ap=l["ap"], ssid=l.get("ssid", ""))
        if l.get("cable_ap"):
            return t("notify.where_port", ap=l["cable_ap"], port=l.get("cable_port", "?"))
        return t("notify.where_router")

    async def track_new(self):
        """MAC v siti, ktere add-on jeste nevidel -> notifikace v HA. AP a router se nepocitaji."""
        try:
            leases = {l["mac"]: l for l in self.lease_list(self.wired_state)}
            present = self.present(self.wired_state) - (self.wired_state or {}).get("ap_macs", set())
            new = self.known.update(present)
            self.known.save()
        except Exception:  # noqa: BLE001
            log.exception("track_new")
            return
        if not new or not self.opts.get("notify_new_devices", True):
            return
        if not self.opts.get("notify_random_mac", True):
            new = [m for m in new if not maint.is_random_mac(m)]
        if not new:
            return
        if not self.ha_lang and self.opts.get("language", "auto") == "auto":
            self.ha_lang = await ha.ha_language()
        t = self._t()
        lines = []
        for m in new:
            l = leases.get(m, {"mac": m})
            net = ("LAN" if l.get("vlan") is None else f"VLAN {l['vlan']}") + (f" ({l['iface']})" if l.get("iface") else "") \
                if l.get("known_net") else ""
            parts = [f"**{l.get('hostname') or t('notify.unknown')}**", f"`{m}`", l.get("ip", ""), net, self.where(l, t)]
            if maint.is_random_mac(m):
                parts.append(t("notify.random_mac"))
            lines.append(" · ".join(p for p in parts if p))
            log.info("Nove zarizeni: %s", lines[-1])
        title = t("notify.title", n=len(new))
        msg = "\n".join(f"- {x}" for x in lines[:20]) + (f"\n- … +{len(lines) - 20}" if len(lines) > 20 else "")
        try:
            await ha.notify(title, msg, notification_id="openwrt_admin_new_" + new[0].replace(":", ""),
                            service=self.opts.get("notify_service", ""))
            self.notify_status = "ok"
        except Exception as e:  # noqa: BLE001
            self.notify_status = f"{type(e).__name__}: {e}"
            log.warning("Upozorneni do HA se nepodarilo odeslat: %s", self.notify_status)

    # ------------------------------------------------------------ aktualizace a zalohy
    def managed(self) -> list[APState]:
        return [d for d in [self.router, *self.aps] if d is not None]

    def update_info(self, d: APState) -> dict:
        """Pro sablony: {checked, pkgs, count, error, release}."""
        u = self.updates.get(d.name) or {}
        return {"checked": u.get("ts", 0), "pkgs": u.get("pkgs") or [], "count": len(u.get("pkgs") or []),
                "error": u.get("error", ""), "release": maint.newer_release(d.release, self.versions) if self.versions else ""}

    def updates_total(self) -> int:
        return sum(len((self.updates.get(d.name) or {}).get("pkgs") or []) for d in self.managed())

    def save_updates(self):
        try:
            UPDATESFILE.write_text(json.dumps(self.updates))
        except OSError as e:
            log.warning("Nelze ulozit %s: %s", UPDATESFILE, e)

    async def check_one(self, d: APState):
        if self.opts.get("demo"):
            return
        async with self.ap_lock(d.name):
            self.updates[d.name] = await maint.check_updates(self.cfg(d.name), self.key, d.pkg or "apk")
        self.save_updates()
        log.info("Aktualizace %s: %d balicku%s", d.name, len(self.updates[d.name]["pkgs"]),
                 f" ({self.updates[d.name]['error']})" if self.updates[d.name]["error"] else "")

    async def refresh_versions(self):
        if time.time() - self.versions_ts < 6 * 3600:
            return
        self.versions_ts = time.time()
        try:
            self.versions = await asyncio.to_thread(maint.fetch_versions)
        except Exception as e:  # noqa: BLE001 - bez internetu jen nebude videt nova verze
            log.info("Verze OpenWrt nezjisteny: %s", e)

    def backups(self, d: APState) -> list[dict]:
        return maint.list_backups(BACKUPS, d.name)

    async def backup_one(self, d: APState) -> tuple[bool, str]:
        if d.name in self.backup_busy:
            return False, "busy"
        self.backup_busy.add(d.name)
        try:
            async with self.ap_lock(d.name):
                ok, msg = await maint.backup(self.cfg(d.name), self.key, BACKUPS, d.name,
                                             int(self.opts.get("backup_keep", 10) or 10))
        finally:
            self.backup_busy.discard(d.name)
        log.info("Zaloha %s: %s", d.name, msg if ok else "chyba: " + msg)
        return ok, msg

    async def maintenance(self):
        """Jednou za cas: kontrola aktualizaci a automaticke zalohy (postupne, ne naraz)."""
        if self.opts.get("demo"):
            return
        now = time.time()
        hours = int(self.opts.get("update_check_hours", 12) or 0)
        if hours > 0:
            await self.refresh_versions()
            for d in self.managed():
                if d.online and now - (self.updates.get(d.name) or {}).get("ts", 0) > hours * 3600:
                    await self.check_one(d)
        days = int(self.opts.get("backup_interval_days", 7) or 0)
        if days > 0:
            for d in self.managed():
                last = next(iter(self.backups(d)), None)
                if d.online and (not last or now - last["ts"] > days * 86400):
                    await self.backup_one(d)

    def bans(self, ap_name: str | None = None) -> list[dict]:
        """Zablokovani klienti: z hostapd list_bans; u AP bez list_bans (starsi hostapd) podle toho, co zadal add-on."""
        now = time.time()
        self.ban_meta = {m: v for m, v in self.ban_meta.items() if v["until"] > now}
        rows: dict[tuple, dict] = {}
        for a in self.aps:
            if not a.online:
                continue
            items = list(a.bans)
            if not a.bans_ok:  # nevime – ukazat blokace zadane z add-onu na AP s tim SSID
                items += [{"mac": m, "ifname": x["ifname"], "ssid": v["ssid"]} for m, v in self.ban_meta.items()
                          for x in a.ssids if x["ssid"] == v["ssid"]]
            for b in items:
                r = rows.setdefault((b["mac"], b["ssid"]), {"mac": b["mac"], "ssid": b["ssid"], "aps": [],
                                                            "until": (self.ban_meta.get(b["mac"]) or {}).get("until", 0),
                                                            "hostname": (self.info.get(b["mac"]) or {}).get("hostname", "")})
                if a.name not in r["aps"]:
                    r["aps"].append(a.name)
        out = sorted(rows.values(), key=lambda r: (r["hostname"] or "~" + r["mac"]).lower())
        return [r for r in out if ap_name is None or ap_name in r["aps"]]

    WAKE_EVERY = 240  # s; switch zapomina MAC po ~5 min, castejsi ping neni potreba
    _last_wake = 0.0

    def wake_ips(self) -> list[str]:
        """IP kabelovych zarizeni, ktera router vidi, ale zadny AP je nema na portu (switch je zapomnel).
        Router je pingne -> pri dalsim nacteni je switch AP zase zna. Jen kdyz nejaky AP umi tabulku switche."""
        if not self.router or not self.router.online or not any(a.arl for a in self.l2()) or self.wired_state is None:
            return []
        now = time.time()
        if now - self._last_wake < self.WAKE_EVERY:
            return []
        self._last_wake = now
        try:
            return [l["ip"] for l in self.lease_list(self.wired_state)
                    if l["online"] and l["ip"] and not l["ap"] and not l["cable_ap"] and not l["is_ap"]
                    and l["known_net"] and l["ip"] != self.router.host.split(":")[0]]
        except Exception:  # diagnosticka vec, nesmi shodit sber
            log.exception("wake_ips")
            return []

    def port_table(self, ap) -> list[dict]:
        """Fyzicke porty zarizeni pro stranku AP/routeru: stav, rychlost, role a zarizeni na portu."""
        if not ap.online or not ap.ports:
            return []
        a = wiredmod.analyze(ap, self.router) if ap.arl else {"uplink": set(), "cpu": set(), "local": {}}
        up = wiredmod.uplink_port(ap, self.router) if ap.kind != "router" else None
        wan_devs = {i.get("device") for i in ap.interfaces if i.get("wan")}
        on_port: dict[str, list[dict]] = {}
        w = self.wired_state or {}
        for d in (w.get("by_ap") or {}).get(ap.name, []):
            on_port.setdefault(str(d["port"]), []).append(d)
        parents = (self.wired_state or {}).get("parent") or {}
        ap_names = {x.mac: x.name for x in self.l2() if x.mac and x is not ap and parents.get(x.name) == ap.name}
        for mac, port in a["local"].items():  # AP zapojene za timto AP
            if mac in ap_names:
                on_port.setdefault(port.split(":")[1], []).append({"mac": mac, "hostname": ap_names[mac], "ap": True})
        # na uplink portu: nadrazene zarizeni (switch / AP), jinak router
        par = (w.get("parent") or {}).get(ap.name) or (self.router.name if self.router and ap.kind != "router" else "")
        out = []
        for p in ap.ports:
            if p["id"] in a["cpu"]:
                role = "cpu"
            elif p["id"] in a["uplink"] or (up and p["id"] == up["id"]):
                role = "uplink"
            elif p["id"] in wan_devs:
                role = "wan"
            else:
                role = "local" if p["kind"] == "switch" or ap.kind != "router" else "lan"
            devs = on_port.get(p["num"], []) if p["kind"] == "switch" else []
            if role == "uplink" and par:
                devs = [{"mac": "", "hostname": par, "ap": True}] + devs
            row = {**p, "role": role, "label": wiredmod.speed_label(p["speed"]), "devices": devs}
            if ap.kind == "switch":  # provoz z citacu SNMP (statistiky)
                row["load_down"], row["load_up"] = self.stats.port_rate(ap.name, p["num"])
            out.append(row)
        return out

    def switch_diag(self, ap) -> dict | None:
        """Diagnostika switche AP pro stranku AP: porty, MAC, uplink/CPU, pamet 'naposledy videno'."""
        if not ap.online:
            return None
        info = self.info or {}
        a = wiredmod.analyze(ap, self.router)
        wifi = {c["mac"] for x in self.aps for c in x.clients}
        ap_macs = {x.mac: x.name for x in self.l2() if x.mac}
        if self.router and self.router.mac:
            ap_macs[self.router.mac] = self.router.name
        ports = []
        for port in sorted(ap.arl, key=lambda p: (p.split(":")[0], int(p.split(":")[1]) if p.split(":")[1].isdigit() else 99)):
            role = "uplink" if port in a["uplink"] else ("cpu" if port in a["cpu"] else "local")
            macs = []
            for m in ap.arl[port]:
                e = info.get(m) or {}
                macs.append({"mac": m, "name": e.get("hostname") or ap_macs.get(m, ""), "ip": e.get("ip", ""),
                             "gw": m in a["gw_macs"], "wifi": m in wifi, "ap": m in ap_macs})
            ports.append({"port": port, "num": port.split(":")[1], "role": role, "macs": macs})
        mem = []
        for m, v in (self.seen or {}).items():
            if v.get("ap") == ap.name:
                e = info.get(m) or {}
                mem.append({"mac": m, "name": e.get("hostname") or v.get("hostname", ""), "ip": e.get("ip") or v.get("ip", ""),
                            "port": v.get("port", "?"), "ts": v.get("ts", 0),
                            "live": m in a["local"]})
        mem.sort(key=lambda x: (str(x["port"]), x["name"] or "~"))
        return {"has_arl": bool(ap.arl), "gw_ip": a["gw_ip"], "gw_found": bool(a["gw_macs"]),
                "uplink": bool(a["uplink"]), "ports": ports, "memory": mem}

    def devices(self) -> list[dict]:
        """Vsechna spravovana zarizeni (AP + router) pro souhrn na prehledu."""
        out = [{"name": a.name, "online": a.online} for a in [*self.switches, *self.aps]]
        if self.router:
            out.insert(0, {"name": self.router.name, "online": self.router.online})
        return out

    def _load_seen(self) -> dict:
        if self.opts.get("demo"):
            return {}
        try:
            return json.loads(SEENFILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_seen(self):
        try:
            tmp = SEENFILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.seen))
            tmp.replace(SEENFILE)
        except OSError as e:
            log.warning("Nelze ulozit %s: %s", SEENFILE, e)

    HA_REFRESH = 600  # s

    async def refresh_ha(self, force: bool = False):
        if not self.opts.get("ha_names", True):
            self.ha_names, self.ha_status = {}, "off"
            return
        if not force and time.time() - self.ha_fetched < self.HA_REFRESH:
            return
        self.ha_fetched = time.time()
        try:
            names = await ha.fetch_names()
            if names or os.environ.get("SUPERVISOR_TOKEN"):
                self.ha_names, self.ha_status = names, "ok"
            else:
                self.ha_status = "off"
        except Exception as e:  # noqa: BLE001 - jmena z HA jsou jen bonus
            self.ha_status = f"{type(e).__name__}: {e}"
            log.warning("Jmena z Home Assistantu: %s", self.ha_status)

    def build_info(self) -> dict[str, dict]:
        """MAC -> {ip, hostname, src, lease}. Jmeno: rucne (names) > Home Assistant > DHCP lease > rezervace
        na routeru. IP: DHCP lease > rezervace > ARP routeru > ARP AP (zarizeni s pevnou IP)."""
        info: dict[str, dict] = {}

        def get(mac):
            return info.setdefault(mac, {"ip": "", "hostname": "", "src": "", "lease": False})
        for ap in self.aps:
            for ip, mac in ap.arp.items():
                if not get(mac)["ip"]:
                    get(mac)["ip"] = ip
        if self.router:
            for ip, mac in self.router.arp.items():
                get(mac)["ip"] = ip
            for mac, h in self.router.static_hosts.items():
                e = get(mac)
                if h.get("ip"):
                    e["ip"] = h["ip"]
                if h.get("name"):
                    e["hostname"], e["src"] = h["name"], "static"
        for mac, l in self.leases.items():
            e = get(mac)
            e["lease"] = True
            if l.get("ip"):
                e["ip"] = l["ip"]
            if l.get("hostname"):
                e["hostname"], e["src"] = l["hostname"], "dhcp"
        for mac, name in self.ha_names.items():
            e = get(mac)
            e["hostname"], e["src"] = name, "ha"
        for n in self.opts.get("names") or []:
            mac = str(n.get("mac", "")).strip().lower().replace("-", ":")
            if len(mac) == 17 and n.get("name"):
                e = get(mac)
                e["hostname"], e["src"] = str(n["name"]), "manual"
        return info

    def present(self, wired: dict | None = None) -> set[str]:
        """MAC, ktere jsou ted opravdu v siti: ARP routeru/AP (kompletni zaznam), Wi-Fi klienti, MAC na portu
        switche. Staticke rezervace a stare DHCP leases o pritomnosti nerikaji nic."""
        p = {c["mac"] for a in self.aps for c in a.clients}
        for a in [*self.aps, self.router]:
            if a is not None:
                p |= set(a.arp.values())
        w = wired or self.wired_state or {}
        for devs in (w.get("by_ap") or {}).values():
            p |= {d["mac"] for d in devs if not d.get("stale")}
        return p

    def _known(self) -> set[str]:
        """MAC, ktere router porad zna (DHCP lease nebo ARP) – pro drzeni 'naposledy videno'."""
        k = set(self.leases)
        if self.router:
            k |= set(self.router.arp.values())
        return k

    def wired(self) -> dict:
        if self.wired_state is None:
            self.info = self.info or self.build_info()
            self.wired_state = wiredmod.locate(self.l2(), self.router, self.info, self.seen, known=self._known())
        return self.wired_state

    def lease_list(self, wired: dict | None = None) -> list[dict]:
        """Zarizeni, ktera zna router (DHCP lease, rezervace, ARP), s Wi-Fi AP/SSID/pasmem nebo AP+portem LAN."""
        wifi = {c["mac"]: c for a in self.aps for c in a.clients}
        wired = wired or self.wired()
        cable = {d["mac"]: (ap, d["port"], d.get("stale")) for ap, devs in wired["by_ap"].items() for d in devs}
        macs = set(self.leases)
        if self.router:
            macs |= set(self.router.arp.values()) | {m for m, h in self.router.static_hosts.items() if h.get("ip")}
        present = self.present(wired)
        resolve = topology.net_resolver(self.router)
        out = []
        for mac in macs:
            e = self.info.get(mac) or {"ip": "", "hostname": "", "src": "", "lease": mac in self.leases}
            c = wifi.get(mac)
            cab = cable.get(mac)
            out.append({"mac": mac, "ip": e["ip"], "hostname": e["hostname"], "src": e["src"], "static": not e["lease"],
                        "ap": c["ap"] if c else "", "ssid": c["ssid"] if c else "", "band": c.get("band", "") if c else "",
                        "cable_ap": cab[0] if cab else "", "cable_port": cab[1] if cab else "",
                        "cable_stale": cab[2] if cab else None,
                        "is_ap": mac in wired["ap_macs"], "online": mac in present})
            n = resolve(e["ip"]) if e["ip"] else None
            out[-1].update(iface=(n or {}).get("iface", ""), vlan=(n or {}).get("vlan"), known_net=bool(n),
                           first_seen=self.known.first_seen(mac))

        def ipkey(x):
            try:
                return (0, tuple(int(p) for p in x["ip"].split(".")))
            except ValueError:
                return (1, x["ip"])
        return sorted(out, key=ipkey)

    def all_clients(self) -> list[dict]:
        out = []
        for ap in self.aps:
            for c in ap.clients:
                e = self.info.get(c["mac"]) or self.leases.get(c["mac"], {})
                r = self.stats.wifi.get((ap.name, c["mac"]))
                out.append({**c, "ip": e.get("ip", ""), "hostname": e.get("hostname", ""), "src": e.get("src", ""),
                            "first_seen": self.known.first_seen(c["mac"]),
                            "down_bps": r[0] if r else None, "up_bps": r[1] if r else None})
        return sorted(out, key=lambda c: (c["hostname"] or "~" + c["mac"]).lower())


store: Store | None = None


async def poller():
    while True:
        try:
            await store.poll()
        except Exception:  # noqa: BLE001 - poller nesmi umrit
            log.exception("Chyba pri sberu dat")
        await asyncio.sleep(max(10, int(store.opts.get("poll_interval", 30))))


async def maintainer():
    await asyncio.sleep(60)  # nejdriv at se nactou zarizeni
    while True:
        try:
            await store.maintenance()
        except Exception:  # noqa: BLE001
            log.exception("Chyba pri udrzbe (aktualizace/zalohy)")
        await asyncio.sleep(600)


async def stats_loop():
    await asyncio.sleep(3)
    while True:
        t0 = time.monotonic()
        try:
            await store.stats_tick()
        except Exception:  # noqa: BLE001
            log.exception("Chyba pri sberu statistik")
        await asyncio.sleep(max(1.0, store.stats_interval - (time.monotonic() - t0)))


async def sensors_loop():
    await asyncio.sleep(20)
    while True:
        if store.opts.get("ha_sensors") and store.stats_on:
            try:
                await store.publish_sensors()
            except Exception:  # noqa: BLE001
                log.exception("Chyba pri odesilani senzoru")
        try:
            iv = max(10, int(store.opts.get("ha_sensors_interval", 30) or 30))
        except (TypeError, ValueError):
            iv = 30
        await asyncio.sleep(iv)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store()
    log.info("AP: %s, router: %s, demo: %s",
             ", ".join(f"{a['name']}={a['host']}" for a in store.ap_items) or "-",
             (f"{store.router_item['name']}={store.router_item['host']}" if store.router_item else "-"),
             store.opts.get("demo"))
    tasks = [asyncio.create_task(poller()), asyncio.create_task(maintainer()),
             asyncio.create_task(stats_loop()), asyncio.create_task(sensors_loop())]
    yield
    for task in tasks:
        task.cancel()
    store._stats_close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------------------------------------------------------------- uzivatel a role
# Ingress (Supervisor) posila identitu prihlaseneho uzivatele HA v hlavickach.
# Add-on je dostupny jen z Ingress IP, takze hlavicky nastavuje HA, ne prohlizec.
H_USER_ID, H_USER_NAME, H_USER_DISPLAY = "X-Remote-User-Id", "X-Remote-User-Name", "X-Remote-User-Display-Name"
ROLE_KEYS = {"full": "role.full", "read": "role.read", "none": "role.none"}


def identify(request: Request, t) -> dict:
    h = request.headers
    user = {"id": h.get(H_USER_ID, ""), "name": h.get(H_USER_NAME, "") or os.environ.get("OWADMIN_DEV_USER", ""),
            "display": h.get(H_USER_DISPLAY, "")}
    full = {u.strip().lower() for u in store.opts.get("full_access") or [] if u.strip()}
    read = {u.strip().lower() for u in store.opts.get("read_access") or [] if u.strip()}
    names = {user["name"].lower(), user["display"].lower()} - {""}
    if not full and not read:
        role, why = "full", t("why.no_roles")
    elif not names:
        role, why = "read", t("why.no_user")
    elif names & full:
        role, why = "full", t("why.in_full")
    elif names & read:
        role, why = "read", t("why.in_read")
    else:
        role, why = "none", t("why.not_listed")
    return {**user, "role": role, "why": why, "label": t(ROLE_KEYS[role])}


# ---------------------------------------------------------------- jazyk
LANG_COOKIE = "owadmin_lang"        # rucni volba v tomto prohlizeci (stranka Nastaveni)
LANG_HA_COOKIE = "owadmin_lang_ha"  # jazyk uzivatele HA, nastavuje skript v base.html z localStorage


def pick_language(request: Request) -> tuple[str, str]:
    """(kod jazyka, zdroj): browser > config > auto (jazyk HA) > Accept-Language > en."""
    lang = i18n.normalize(request.cookies.get(LANG_COOKIE))
    if lang:
        return lang, "browser"
    cfg = i18n.normalize(str(store.opts.get("language") or "auto")) if store else ""
    if cfg:
        return cfg, "config"
    lang = i18n.normalize(request.cookies.get(LANG_HA_COOKIE))
    if lang:
        return lang, "auto"
    return (i18n.from_accept_language(request.headers.get("Accept-Language")) or i18n.DEFAULT), "auto"


@app.middleware("http")
async def ingress_only(request: Request, call_next):
    if not ALLOW_ALL and request.client and request.client.host not in INGRESS_IPS:
        return JSONResponse({"detail": "Pouze pres Home Assistant Ingress"}, status_code=403)
    if request.url.path.startswith("/static/") or store is None:
        return await call_next(request)
    lang, source = pick_language(request)
    t = i18n.translator(lang)
    request.state.lang, request.state.lang_source, request.state.t = lang, source, t
    user = identify(request, t)
    request.state.user = user
    actions.ACTOR.set(user["name"] or user["display"] or "")  # do syslogu zarizeni pri akcich
    if user["role"] == "none":
        log.warning("Odepreno: %s (%s) %s %s", user["name"] or "?", user["display"], request.method, request.url.path)
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        return templates.TemplateResponse(request, "denied.html", {"base": base, "user": user, "t": t, "lang": lang},
                                          status_code=403)
    return await call_next(request)


def require_full(request: Request):
    if request.state.user["role"] != "full":
        raise HTTPException(403, request.state.t("msg.no_permission"))


def who(request: Request) -> str:
    u = request.state.user
    return u["name"] or u["display"] or "?"


# ---------------------------------------------------------------- sablony

def fmt_bytes(n) -> str:
    if n is None:
        return "–"
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@pass_context
def fmt_duration(ctx, s) -> str:
    if s is None:
        return "–"
    t = ctx.get("t") or i18n.translator(i18n.DEFAULT)
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return t("fmt.d_h", d=d, h=h)
    if h:
        return t("fmt.h_m", h=h, m=m)
    return t("fmt.m", m=m)


@pass_context
def fmt_interval(ctx, v) -> str:
    t = ctx.get("t") or i18n.translator(i18n.DEFAULT)
    v = int(v or 0)
    if v <= 0:
        return t("setup.refresh_off")
    return t("fmt.sec", s=v) if v < 60 else t("fmt.min", m=v // 60)


def fmt_rate(kbit) -> str:
    if not kbit:
        return "–"
    return f"{kbit / 1000:.0f} Mb/s"


def signal_class(sig) -> str:
    if sig is None:
        return ""
    return "good" if sig >= -60 else "ok" if sig >= -72 else "bad"


@pass_context
def ago(ctx, ts) -> str:
    t = ctx.get("t") or i18n.translator(i18n.DEFAULT)
    if not ts:
        return t("fmt.never")
    s = int(time.time() - ts)
    if s < 120:
        return t("fmt.ago_s", s=s)
    if s < 7200:
        return t("fmt.ago_m", m=s // 60)
    if s < 172800:
        return t("fmt.ago_h", h=s // 3600)
    return t("fmt.ago_d", d=s // 86400)


@pass_context
def fmt_band(ctx, b) -> str:
    """Pasmo se interne drzi jako '2,4 GHz'; v jazycich s desetinnou teckou '2.4 GHz'."""
    b = b or ""
    return b if ctx.get("lang", "cs") == "cs" else b.replace(",", ".")


templates.env.filters.update(band=fmt_band, bytes=fmt_bytes, duration=fmt_duration, rate=fmt_rate, interval=fmt_interval,
                             sigclass=signal_class, ago=ago)
templates.env.filters["speed"] = lambda mbps: (f"{mbps / 1000:g} Gb/s" if mbps >= 1000 else f"{mbps} Mb/s") if mbps else "–"
templates.env.filters["left"] = lambda ts: max(0, int(ts - time.time()))  # zbyva sekund
templates.env.filters["bps"] = statsmod.fmt_bps
templates.env.filters["ms"] = lambda v: "–" if v is None else (f"{v:.1f} ms" if v < 10 else f"{v:.0f} ms")
def new_hours() -> int:
    """Jak dlouho ma zarizeni stitek „nove“ (volba new_device_hours, 0 = nikdy)."""
    try:
        return max(0, int(store.opts.get("new_device_hours", 24))) if store else 24
    except (TypeError, ValueError):
        return 24


templates.env.tests["recent"] = lambda ts, hours=None: bool(ts) and time.time() - ts < (new_hours() if hours is None else hours) * 3600
# verze statickych souboru do URL – prohlizec/Ingress jinak drzi stare style.css z cache
STATIC_VER = hashlib.md5(b"".join((BASE_DIR / "static" / f).read_bytes() for f in ("style.css", "htmx.min.js"))).hexdigest()[:10]


REFRESH_CHOICES = [0, 10, 15, 30, 60, 120, 300]
REFRESH_COOKIE = "owadmin_refresh"


def refresh_interval(request: Request) -> tuple[int, bool]:
    """(interval v s, 0 = vypnuto; True = vlastni hodnota prohlizece z cookie)."""
    c = request.cookies.get(REFRESH_COOKIE, "")
    if c.isdigit() and int(c) in REFRESH_CHOICES:
        return int(c), True
    try:
        v = int(store.opts.get("refresh_interval", 30))
    except (TypeError, ValueError):
        v = 30
    return (0 if v <= 0 else max(5, v)), False


def render(request: Request, name: str, **ctx):
    base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    refresh, refresh_own = refresh_interval(request)
    return templates.TemplateResponse(request, name, {
        "base": base, "store": store, "now": time.time(), "autorefresh": True, "static_ver": STATIC_VER,
        "refresh": refresh, "refresh_own": refresh_own, "refresh_choices": REFRESH_CHOICES, "refresh_cookie": REFRESH_COOKIE,
        "t": request.state.t, "lang": request.state.lang, "lang_source": request.state.lang_source,
        "languages": i18n.NAMES, "lang_cookie": LANG_COOKIE, "lang_ha_cookie": LANG_HA_COOKIE,
        "user": request.state.user, "can_edit": request.state.user["role"] == "full", **ctx})


# ---------------------------------------------------------------- stranky

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    clients = store.all_clients()
    return render(request, "dashboard.html", page="dashboard", clients=clients, devices=store.devices())


@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, q: str = "", ssid: str = "", ap: str = "", band: str = ""):
    clients = store.all_clients()
    if q:
        ql = q.lower()
        clients = [c for c in clients if ql in c["mac"] or ql in c["hostname"].lower() or ql in c["ip"]]
    if band:
        clients = [c for c in clients if c.get("band") == band]
    if ssid:
        clients = [c for c in clients if c["ssid"] == ssid]
    if ap:
        clients = [c for c in clients if c["ap"] == ap]
    ssids = sorted({c["ssid"] for a in store.aps for c in a.clients})
    return render(request, "clients.html", page="clients", clients=clients,
                  q=q, ssid=ssid, ap=ap, band=band, ssids=ssids,
                  bands=sorted({c.get("band") for a in store.aps for c in a.clients if c.get("band")}))


@app.get("/ap/{name}", response_class=HTMLResponse)
async def ap_page(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind == "router":
        leases = store.lease_list()
        counts: dict[str, int] = {}
        for l in leases:
            if l["online"] and l["iface"]:
                counts[l["iface"]] = counts.get(l["iface"], 0) + 1
        return render(request, "router.html", page="ap:" + name, ap=ap, leases=leases, iface_counts=counts,
                      ports=store.port_table(ap))
    if ap.kind == "switch":
        return render(request, "switch.html", page="ap:" + name, ap=ap, ports=store.port_table(ap),
                      diag=store.switch_diag(ap), sw=next((x for x in store.switch_items if x["name"] == name), {}))
    clients = [c for c in store.all_clients() if c["ap"] == name]
    return render(request, "ap.html", page="ap:" + name, ap=ap, clients=clients, diag=store.switch_diag(ap),
                  ports=store.port_table(ap))


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, clients: str = "1", empty: str = "0"):
    show_clients, show_empty = clients != "0", empty == "1"
    w = store.wired()
    router_wired = [l for l in store.lease_list(w)
                    if l["online"] and not l["ap"] and not l["cable_ap"] and not l["is_ap"]
                    and (l["known_net"] or not l["ip"] or not store.router)]  # IP mimo LAN/VLAN routeru (WAN) ne
    speeds = {}
    l2 = store.l2()
    for a in l2:
        up = wiredmod.uplink_port(a, store.router) if a.online else None
        if up and up["speed"]:
            speeds[a.name] = up["speed"]
    ap_wired = {}
    resolve = topology.net_resolver(store.router)
    for name, devs in w["by_ap"].items():
        a = next((x for x in l2 if x.name == name), None)
        ap_wired[name] = []
        for d in devs:
            e = {**d, "speed": ((wiredmod.port_by_num(a, d["port"]) or {}).get("speed", 0) if a and not d.get("stale") else 0)}
            if a is not None and a.kind == "switch" and not d.get("stale"):
                e["load"] = store.stats.port_rate(a.name, d["port"])  # provoz na portu switche
            n = resolve(d["ip"]) if d.get("ip") else None
            if n:
                e["vlan"] = n["vlan"]  # barva podle site (DMZ, IoT…)
            ap_wired[name].append(e)
    topo = topology.build(store.router, l2, router_wired, request.state.t, clients=store.all_clients(),
                          show_clients=show_clients, show_empty=show_empty,
                          ap_wired=ap_wired, parent=w["parent"], speeds=speeds,
                          loads=store.link_loads() if store.stats_on else {})
    return render(request, "map.html", page="map", topo=topo, show_clients=show_clients, show_empty=show_empty)


@app.get("/radios", response_class=HTMLResponse)
async def radios_page(request: Request, plan: str = "", dfs: str = ""):
    """plan=1: formular predvyplneny navrhem (kanaly, sirky, vykon) – stranka se pak sama neobnovuje."""
    pl = radiomod.propose(store.aps, store.scan_cells(), dfs_ok=dfs == "1") if plan == "1" else None
    countries = sorted({str(r.get("country") or "") for a in store.aps for r in a.radios} - {""})
    return render(request, "radios.html", page="radios", rv=store.radios_view(), edit=store.radios_edit(pl),
                  plan=pl, plan_dfs=dfs == "1", countries=countries, autorefresh=not pl)


@app.post("/radios/preview", response_class=HTMLResponse)
async def radios_preview(request: Request):
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    form = await request.form()
    country = str(form.get("country") or "").strip().upper() or None
    plan: dict[str, list[str]] = {}
    rows = []
    for a in store.aps:
        if not a.online:
            continue
        for r in a.radios:
            k = f"{a.name}|{r['name']}"
            if f"{k}|channel" not in form:
                continue
            ch, ht, tx = (str(form.get(f"{k}|{f}") or "") for f in ("channel", "htmode", "txpower"))
            new = {"channel": ch if ch and ch != str(r.get("channel", "")) else None,
                   "htmode": ht if ht and ht != str(r.get("htmode") or "") else None,
                   "txpower": tx if tx and tx != str(r.get("txpower") or "auto") else None,
                   "country": country if country and country != str(r.get("country") or "") else None}
            if not any(new.values()):
                continue
            err = radiomod.validate_radio(r, *new.values())
            if err:
                return result(request, title, actions.Result(False, f"{a.name} {r['name']}: {t(err)}"))
            cmds = radiomod.radio_cmds(r["name"], *new.values())
            plan.setdefault(a.name, []).extend(cmds)
            old = {"channel": r.get("channel"), "htmode": r.get("htmode"), "txpower": r.get("txpower") or "auto",
                   "country": r.get("country") or "–"}
            rows.append({"ap": a.name, "label": f"{r['name']} · {r.get('band', '')}",
                         "changes": [f"{t('radio.f_' + f)}: {old[f]} → {v}" for f, v in new.items() if v is not None],
                         "cmds": cmds,
                         "dfs": new["channel"] not in (None, "auto") and radiomod.is_dfs(int(new["channel"]))})
    if not rows:
        return result(request, title, actions.Result(False, t("msg.no_change")))
    token = store.add_pending("radio", plan)
    note = t("radio.apply_note") + (" " + t("radio.apply_dfs_note") if any(x["dfs"] for x in rows) else "")
    return render(request, "_cmd_preview.html", rows=rows, token=token, note=note)


@app.post("/pending/apply", response_class=HTMLResponse)
async def pending_apply(request: Request, token: str = Form(...)):
    """Provede cekajici uci zmenu (radia, nove SSID) po nahledu – na kazdem AP zvlast, AP paralelne."""
    require_htmx(request)
    t = request.state.t
    title = t("preview.apply_title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    entry = store.pending_cmds.pop(token, None)
    if not entry or time.time() - entry[0] > 600:
        return result(request, title, actions.Result(False, t("msg.preview_expired")))
    _, kind, plan = entry
    log.info("%s: %s na %s", who(request), kind, ", ".join(plan))
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, title, actions.Result(True, "\n".join(f"{ap}: OK (demo)" for ap in plan)))

    async def one(ap_name, cmds):
        async with store.ap_lock(ap_name):
            r = await actions.apply_uci(store.cfg(ap_name), store.key, cmds, t)
        return f"== {ap_name} ==\n{r.output}", r.ok

    res = await asyncio.gather(*(one(a, c) for a, c in plan.items()))

    async def repoll():
        await asyncio.sleep(10)  # wifi reload chvili trva
        await store.poll()
    asyncio.create_task(repoll())
    return result(request, title, actions.Result(all(ok for _, ok in res), "\n\n".join(o for o, _ in res)))


@app.post("/ssids/new/preview", response_class=HTMLResponse)
async def ssid_new_preview(request: Request, ssid: str = Form(""), key: str = Form(""), encryption: str = Form("psk2"),
                           network: str = Form(""), hidden: str = Form("")):
    """Nove SSID na vybranych radiich (nova wifi-iface sekce). Heslo se do stranky ani logu nevraci."""
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    form = await request.form()
    targets = form.getlist("target")  # "ap|radio"
    plan: dict[str, list[str]] = {}
    rows = []
    for target in targets:
        ap_name, _, radio_name = target.partition("|")
        ar = store.radio(ap_name, radio_name)
        if not ar:
            continue
        a, r = ar
        nets = {i["name"] for i in a.interfaces if not i.get("wan")}
        if nets and network not in nets:
            return result(request, title, actions.Result(False, t("ssid.err_network", ap=a.name, net=network)))
        if any(w["ssid"] == ssid and w["device"] == r["name"] for w in a.wifi_ifaces):
            return result(request, title, actions.Result(False, t("ssid.err_exists", ap=a.name, ssid=ssid, radio=r["name"])))
        existing = {w["section"] for w in a.wifi_ifaces} | {p.split("=")[0].split(".")[1] for p in plan.get(a.name, [])
                                                              if p.endswith("=wifi-iface")}
        n = actions.NewSsid(device=r["name"], ssid=ssid, key=key, encryption=encryption, network=network,
                            hidden=hidden == "1")
        n.section = actions.new_section(ssid, r["name"], existing)
        err = actions.validate_new(n, t)
        if err:
            return result(request, title, actions.Result(False, err))
        cmds = actions.new_ssid_cmds(n)
        plan.setdefault(a.name, []).extend(cmds)
        rows.append({"ap": a.name, "label": f"{r['name']} · {r.get('band', '')}",
                     "changes": [t("ssid.new_row", ssid=ssid, net=network, enc=encryption)], "cmds": [actions.mask(c) for c in cmds]})
    if not rows:
        return result(request, title, actions.Result(False, t("ssid.err_no_target")))
    token = store.add_pending("new-ssid", plan)
    return render(request, "_cmd_preview.html", rows=rows, token=token, note=t("ssid.new_note"))


@app.post("/radios/scan", response_class=HTMLResponse)
async def radios_scan(request: Request, ap: str = Form(""), radio: str = Form("")):
    """Sken okolnich siti (iwinfo <if> scan) – jen s volbou radio_scan a plnym pristupem.
    Radio na chvili odskoci z kanalu, klienti muzou zaznamenat kratky vypadek."""
    require_htmx(request)
    t = request.state.t
    title = t("radio.scan")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    if not (store.opts.get("radio_scan") or store.opts.get("demo")):
        return result(request, title, actions.Result(False, t("radio.scan_disabled")))
    targets = [(a, r) for a in store.aps if a.online for r in a.radios
               if r.get("up") and (not ap or a.name == ap) and (not radio or r["name"] == radio)]
    if not targets:
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    log.info("%s: sken okoli %s", who(request), ", ".join(f"{a.name}/{r['name']}" for a, r in targets))
    by_ap: dict[str, list] = {}
    for a, r in targets:
        by_ap.setdefault(a.name, []).append((a, r))

    async def per_ap(lst):  # na jednom AP postupne, AP navzajem paralelne
        return [await store.scan_radio(a, r) for a, r in lst]
    res = [x for lst in await asyncio.gather(*(per_ap(v) for v in by_ap.values())) for x in lst]
    resp = result(request, title, actions.Result(all(not x["error"] for x in res),
                                                 t("radio.scan_done", n=sum(len(x["cells"]) for x in res))))
    resp.headers["HX-Refresh"] = "true"
    return resp


@app.get("/services")
async def services_redirect(request: Request):
    """Stara adresa (0.18.0) – sluzby jsou ted zalozka u kazdeho zarizeni."""
    devs = store.svc_devices()
    base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    return RedirectResponse(f"{base}/ap/{quote(devs[0].name)}/services" if devs else f"{base}/", status_code=303)


def _svc_data(ap: APState) -> dict:
    return {"name": ap.name, "kind": ap.kind, "online": ap.online, **(store.svc.get(ap.name) or {})}


SVC_FRESH = 60.0  # s – do te doby se zalozka ukaze hned z pameti (po akci se nacita vzdy znovu)


@app.get("/ap/{name}/services", response_class=HTMLResponse)
async def device_services(request: Request, name: str):
    """Zalozka Sluzby: s cerstvymi daty hned, jinak stranka hned a obsah se dotahne (/services/body)."""
    ap = store.ap(name)
    if ap.kind == "switch":
        raise HTTPException(404, "device not found")
    fresh = time.time() - (store.svc.get(name) or {}).get("ts", 0) < SVC_FRESH
    return render(request, "services.html", page="ap:" + name, ap=ap, d=_svc_data(ap) if fresh else None,
                  autorefresh=False)


@app.get("/ap/{name}/services/body", response_class=HTMLResponse)
async def device_services_body(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind == "switch":
        raise HTTPException(404, "device not found")
    await store.load_services(only=name, max_age=SVC_FRESH)
    return render(request, "_services_body.html", ap=ap, d=_svc_data(ap))


async def dhcp_apply(ap: str, cmds: list[str], summary: str, t, script: str | None = None) -> actions.Result:
    """Zmena DHCP: pred prvni zmenou dne zaloha konfigurace routeru, pak uci + commit + reload dnsmasq."""
    dev = store.ap(ap)
    out = []
    today = time.strftime("%Y-%m-%d")
    if not any(time.strftime("%Y-%m-%d", time.localtime(b["ts"])) == today for b in store.backups(dev)):
        ok, msg = await store.backup_one(dev)
        out.append(t("dhcp.backup_done", file=msg) if ok else t("dhcp.backup_failed", err=msg))
        if not ok and msg != "busy":
            return actions.Result(False, "\n".join(out))
    lock = store.ap_lock(ap)
    async with lock:
        r = await actions.run(store.cfg(ap), store.key, script or dhcpmod.apply_script(cmds, t("msg.reverted")),
                              timeout=120 if script else 60, t=t, summary=f"{summary} ({ap})")
    r.output = "\n".join([*out, r.output])
    return r


def _dhcp_ctx(ap: APState) -> dict:
    m = store.dhcp or {}
    if not m.get("hosts") and m.get("error"):
        return {"m": m}
    online = {x["mac"]: x["online"] for x in store.lease_list()}
    reserved = {mac for h in m["hosts"] for mac in h["macs"]}
    suggest = {n: dhcpmod.suggest_ip(m, n, store.arp_ips()) for n in m["_nets"]}
    names = {x["mac"]: x["hostname"] for x in store.lease_list() if x["hostname"]}
    leases = [{**l, "name": l["hostname"] or names.get(l["mac"], ""), "reserved": l["mac"] in reserved,
               "left": l["expires"] - m["now"]} for l in m["leases"]]
    pools = [p for p in m["pools"] if not p["wan"] and p["subnet"]]
    for nd in m["no_dhcp"]:
        size = ipaddress.ip_network(nd["subnet"]).num_addresses
        start = 100 if size > 200 else 2
        nd["default_start"], nd["default_limit"] = start, max(1, min(150, size - start - 2))
    apk = m.get("apknew") or []
    apk_dhcp = [f for f in apk if f.rsplit("/", 1)[-1].split(".")[0].split("-")[0] == "dhcp"]
    apk_other = [f for f in apk if f not in apk_dhcp]
    return {"m": m, "online": online, "suggest": suggest, "leases": leases, "pools": pools,
            "apk_dhcp": apk_dhcp, "apk_other": apk_other,
            "nets": [n for n in m["_nets"] if not n.startswith("wan")], "tight": [p for p in pools if p["size"] and not p["ignore"] and p["free"] <= 5]}


@app.get("/ap/{name}/dhcp", response_class=HTMLResponse)
async def device_dhcp(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    fresh = store.dhcp and time.time() - store.dhcp["ts"] < SVC_FRESH
    return render(request, "dhcp.html", page="ap:" + name, ap=ap, ready=bool(fresh), autorefresh=False,
                  **(_dhcp_ctx(ap) if fresh else {}))


@app.get("/ap/{name}/dhcp/body", response_class=HTMLResponse)
async def device_dhcp_body(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    await store.load_dhcp(max_age=SVC_FRESH)
    return render(request, "_dhcp_body.html", ap=ap, **_dhcp_ctx(ap))


def _dns_ctx(ap: APState) -> dict:
    m = store.dhcp or {}
    if not m.get("dnsmasq") and m.get("error"):
        return {"m": m}
    dom = m["dnsmasq"].get("domain") or ""
    hostnames = sorted({h["name"] for h in m["hosts"] if h["name"] and h["dns"]}, key=str.lower)
    return {"m": m, "dm": m["dnsmasq"], "wan": m["wan"], "dom": dom, "hostnames": hostnames, "presets": dnsmod.PRESETS,
            "known": sorted(dnsmod.local_names(m))}


@app.get("/ap/{name}/dns", response_class=HTMLResponse)
async def device_dns(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    fresh = store.dhcp and time.time() - store.dhcp["ts"] < SVC_FRESH
    return render(request, "dns.html", page="ap:" + name, ap=ap, ready=bool(fresh), autorefresh=False,
                  **(_dns_ctx(ap) if fresh else {}))


@app.get("/ap/{name}/dns/body", response_class=HTMLResponse)
async def device_dns_body(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    await store.load_dhcp(max_age=SVC_FRESH)
    return render(request, "_dns_body.html", ap=ap, **_dns_ctx(ap))


@app.post("/dns/lookup", response_class=HTMLResponse)
async def dns_lookup(request: Request, name: str = Form("")):
    """Test prekladu pres dnsmasq na routeru – jen cteni, smi i uzivatel jen pro cteni."""
    require_htmx(request)
    t = request.state.t
    n = dnsmod.norm_name(name)
    if not n or not store.router:
        return result(request, t("dns.lookup"), actions.Result(False, t("dns.err_name")))
    if store.opts.get("demo"):
        out, ok = dnsmod.demo_lookup(store.dhcp or {}, n), True
    else:
        r = await actions.run(store.cfg(store.router.name), store.key, dnsmod.lookup_cmd(n), timeout=20, t=t, audit=False)
        out, ok = r.output, r.ok
    res = dnsmod.parse_lookup(out)
    return render(request, "_dns_lookup.html", qname=n, res=res, out=out, ok=ok)


async def _upstream_tests(servers: list[str], t) -> dict[str, bool]:
    """Router se zepta kazdeho noveho serveru (jen cteni). Demo: 192.0.2.x a 198.51.100.x neodpovi."""
    if not servers:
        return {}
    if store.opts.get("demo"):
        return {x: not x.startswith(("192.0.2.", "198.51.100.")) for x in servers if "#" not in x}
    r = await actions.run(store.cfg(store.router.name), store.key, dnsmod.test_cmd(servers), timeout=40, t=t, audit=False)
    return dnsmod.parse_test(r.output)


async def _upstream_plan(t, form, m: dict, op: str):
    """Nadrazene DNS: v dnsmasq (dns_up, fwd_add, fwd_del) nebo na WAN (wan_dns). Vraci
    (prikazy, co ukazat v nahledu, zmeny, varovani, info) nebo chybovou zpravu."""
    dm, wan = m["dnsmasq"], m["wan"]
    warns: list[str] = []
    servers, forwards = list(dm["servers"]), list(dm["forwards"])
    if op in ("dns_up", "wan_dns"):
        raw = dnsmod.split_list(str(form.get("servers") or ""))
        servers = []
        for x in raw:
            v = dnsmod.norm_server(x)
            if not v:
                return t("dns.err_server", s=x)
            if v not in servers:
                servers.append(v)
        if len(servers) > 8:
            return t("dns.err_too_many")
    elif op == "fwd_add":
        v = dnsmod.norm_forward(str(form.get("domain") or ""), str(form.get("server") or ""))
        if not v:
            return t("dns.err_forward")
        if v in forwards:
            return t("msg.no_change")
        forwards.append(v)
    elif op == "fwd_del":
        v = str(form.get("value") or "")
        if v not in forwards:
            return t("msg.bad_request")
        forwards.remove(v)
    if op == "wan_dns":
        if not wan["exists"]:
            return t("dns.err_no_wan")
        if servers == wan["dns"]:
            return t("msg.no_change")
    elif servers == dm["servers"] and forwards == dm["forwards"]:
        return t("msg.no_change")
    new = [x for x in servers if x not in (wan["dns"] if op == "wan_dns" else dm["servers"])]
    if op == "fwd_add":
        new = [forwards[-1].rsplit("/", 1)[-1]]
    tests = await _upstream_tests(new, t)
    bad = [x for x, ok in tests.items() if not ok]
    if bad:
        warns.append(t("dns.warn_noanswer", s=", ".join(bad)))
    if servers and op in ("dns_up", "wan_dns") and tests and all(not v for v in tests.values()) \
            and len(tests) == len([x for x in servers if "#" not in x]):
        return t("dns.err_none_answer")
    if any("#" in x for x in servers):
        warns.append(t("dns.warn_port"))
    if op == "wan_dns":
        cmds = dnsmod.wan_dns_cmds(wan["proto"], servers)
        script = dnsmod.detached(dnsmod.checked_script(cmds, dnsmod.wan_rollback(wan), "network", "/etc/init.d/network reload",
                                                       3, t("dns.applied_ok"), t("dns.rolled_back"), t("msg.reverted")))
        if dm["noresolv"] and dm["servers"]:
            warns.append(t("dns.warn_wan_unused"))
        shown = [*cmds, "uci commit network", "/etc/init.d/network reload", t("dns.shown_check")]
        changes = [t("dns.do_wan", s=", ".join(servers) or t("dns.provider"))]
        return cmds, shown, changes, warns, {"script": script, "servers": servers}
    cmds = dnsmod.dnsmasq_servers_cmds(dm["id"], servers, forwards)
    script = dnsmod.checked_script(cmds, dnsmod.dnsmasq_rollback(dm), "dhcp", "/etc/init.d/dnsmasq reload", 1,
                                   t("dns.applied_ok"), t("dns.rolled_back"), t("msg.reverted"))
    shown = [*cmds, "uci commit dhcp", "/etc/init.d/dnsmasq reload", t("dns.shown_check")]
    if op == "dns_up":
        changes = [t("dns.do_up", s=", ".join(servers)) if servers else t("dns.do_up_wan")]
    elif op == "fwd_add":
        changes = [t("dns.do_fwd", v=forwards[-1])]
    else:
        changes = [t("dns.do_fwd_del", v=str(form.get("value")))]
    return cmds, shown, changes, warns, {"script": script, "servers": servers, "forwards": forwards}


async def safe_apply(ap: str, pkgs: list[str], cmds: list[str], reload: list[str], summary: str, t,
                     check: str | None = None, timeout: int = 90) -> actions.Result:
    """Ulozit s pojistkou: zaloha dne, odpocet na zarizeni, zmena na pozadi, nove spojeni a potvrzeni.
    Kdyz se aplikace po zmene nepripoji, zarizeni se po `timeout` s samo vrati."""
    dev = store.ap(ap)
    out = []
    today = time.strftime("%Y-%m-%d")
    if not any(time.strftime("%Y-%m-%d", time.localtime(b["ts"])) == today for b in store.backups(dev)):
        ok, msg = await store.backup_one(dev)
        out.append(t("dhcp.backup_done", file=msg) if ok else t("dhcp.backup_failed", err=msg))
        if not ok and msg != "busy":
            return actions.Result(False, "\n".join(out))
    tok = safeapply.new_token()
    lock = store.ap_lock(ap)
    async with lock:
        r = await actions.run(store.cfg(ap), store.key, safeapply.arm_script(tok, pkgs, cmds, reload, timeout, check=check),
                              timeout=60, t=t, summary=f"{summary} ({ap})")
    if not r.ok or f"ARMED {tok}" not in r.output:
        return actions.Result(False, "\n".join([*out, t("safe.failed"), r.output[-400:]]))
    out.append(t("safe.armed", s=timeout))
    await asyncio.sleep(4)
    deadline = time.time() + timeout - 15
    while time.time() < deadline:
        c = await actions.run(store.cfg(ap), store.key, safeapply.confirm_cmd(tok), timeout=10, t=t, audit=False)
        if c.ok and "CONFIRMED" in c.output:
            out.append(t("safe.confirmed"))
            return actions.Result(True, "\n".join(out))
        if c.ok and "GONE" in c.output:
            break
        await asyncio.sleep(5)
    out.append(t("safe.not_confirmed", s=timeout))
    return actions.Result(False, "\n".join(out))


def _fw_ctx(ap: APState) -> dict:
    m = store.fw or {}
    return {"fw": m}


@app.get("/ap/{name}/firewall", response_class=HTMLResponse)
async def device_firewall(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    fresh = store.fw and time.time() - store.fw["ts"] < SVC_FRESH
    return render(request, "firewall.html", page="ap:" + name, ap=ap, ready=bool(fresh), autorefresh=False,
                  **(_fw_ctx(ap) if fresh else {}))


@app.get("/ap/{name}/firewall/body", response_class=HTMLResponse)
async def device_firewall_body(request: Request, name: str):
    ap = store.ap(name)
    if ap.kind != "router":
        raise HTTPException(404, "device not found")
    await store.load_fw(max_age=SVC_FRESH)
    return render(request, "_firewall_body.html", ap=ap, **_fw_ctx(ap))


@app.post("/firewall/preview", response_class=HTMLResponse)
async def firewall_preview(request: Request, op: str = Form(""), id: str = Form(""), enable: str = Form("")):
    """Nahled zmeny firewallu (zatim zapnout/vypnout pravidlo nebo presmerovani)."""
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    r, m = store.router, store.fw
    if not r or not m or m.get("error") and not m.get("zones"):
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    if op != "toggle" or m.get("fwver", "fw4") != "fw4":
        return result(request, title, actions.Result(False, t("fw.w_fw3") if m.get("fwver", "fw4") != "fw4" else t("msg.bad_request")))
    it = next((x for x in m["rules"] + m["redirects"] if x["id"] == id), None)
    if not it:
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    on = enable == "1"
    if on == it["enabled"]:
        return result(request, title, actions.Result(False, t("msg.no_change")))
    cmds = fwmod.toggle_cmds(it["id"], on)
    changes = [t("fw.do_on" if on else "fw.do_off", name=it["name"])]
    warns = []
    if it in m["redirects"] and on:
        warns.append(t("fw.warn_open", name=it["name"], port=fwmod.fmt_ports(it["src_dport"])))
    token = store.add_svc_pending(r.name, cmds, {"op": "fw_toggle", "uci": "firewall", "id": it["id"], "enable": on,
                                                 "changes": changes})
    shown = [*cmds, "uci commit firewall", fwmod.CHECK, *fwmod.RELOAD, t("safe.shown")]
    return render(request, "_svc_preview.html", ap=r.name, cmds=shown, changes=changes, token=token,
                  note=t("fw.note"), warns=warns)


@app.post("/dhcp/preview", response_class=HTMLResponse)
async def dhcp_preview(request: Request):
    """Nahled zmeny DHCP (staticka zapujcka, rozsah). Chyby zastavi, varovani se jen ukazou."""
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    r = store.router
    m = store.dhcp
    if not r or not m or not m.get("hosts") and m.get("error"):
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    form = await request.form()
    op = str(form.get("op") or "")
    info: dict = {"op": op, "uci": "dhcp"}
    warns: list[str] = []
    if op in ("host_add", "host_edit"):
        name = str(form.get("name") or "").strip()
        raw_mac = str(form.get("mac") or "")
        mac = dhcpmod.norm_mac(raw_mac) if raw_mac.strip() else ""
        ip = str(form.get("ip") or "").strip()
        dns = form.get("dns") == "1"
        h = None
        if op == "host_edit":
            h = next((x for x in m["hosts"] if x["id"] == str(form.get("id") or "")), None)
            if not h:
                return result(request, title, actions.Result(False, t("msg.bad_request")))
            if not raw_mac.strip():
                mac = h["macs"][0] if h["macs"] else ""
        if mac is None:
            return result(request, title, actions.Result(False, t("dhcp.err_mac")))
        errs, warns = dhcpmod.check_host(m, name, mac, ip, h["id"] if h else None, t)
        if errs:
            return result(request, title, actions.Result(False, "\n".join(errs)))
        cmds = dhcpmod.host_add_cmds(name, mac, ip, dns) if op == "host_add" else dhcpmod.host_edit_cmds(h, name, mac, ip, dns)
        if not cmds:
            return result(request, title, actions.Result(False, t("msg.no_change")))
        info.update(name=name, mac=mac, ip=ip, dns=dns, id=h["id"] if h else "")
        changes = [t("dhcp.do_add", name=name, mac=mac.upper(), ip=ip) if op == "host_add" else
                   t("dhcp.do_edit", name=h["name"], new=f"{name} · {mac.upper()} · {ip}" + (" · DNS" if dns else ""))]
    elif op == "host_del":
        h = next((x for x in m["hosts"] if x["id"] == str(form.get("id") or "")), None)
        if not h:
            return result(request, title, actions.Result(False, t("msg.bad_request")))
        cmds = dhcpmod.host_del_cmds(h)
        info.update(id=h["id"])
        changes = [t("dhcp.do_del", name=h["name"] or h["id"], ip=h["ip"])]
    elif op == "pool":
        iface = str(form.get("iface") or "")
        if not dhcpmod.NET_RE.match(iface):
            return result(request, title, actions.Result(False, t("msg.bad_request")))
        p = next((x for x in m["pools"] if x["iface"] == iface), None)
        try:
            start, limit = int(form.get("start") or 0), int(form.get("limit") or 0)
        except ValueError:
            return result(request, title, actions.Result(False, t("dhcp.err_range", max="?")))
        leasetime = str(form.get("leasetime") or "").strip()
        enabled = form.get("enabled") == "1"
        errs = dhcpmod.check_pool(m, iface, start, limit, leasetime, t)
        if errs:
            return result(request, title, actions.Result(False, "\n".join(errs)))
        cmds = dhcpmod.pool_cmds(p, iface, start, limit, leasetime, enabled)
        if not cmds:
            return result(request, title, actions.Result(False, t("msg.no_change")))
        reserved_out = [h for h in m["hosts"] if h["net"] == iface]
        n = m["_nets"][iface]["net"]
        rng = dhcpmod.pool_range(n, start, limit)
        if rng:
            inside = [h["name"] for h in reserved_out if dhcpmod._in(h["ip"], int(rng[0]), int(rng[1]))]
            if inside:
                warns.append(t("dhcp.warn_pool_res", n=len(inside)))
        if p and not enabled and not p["ignore"]:
            warns.append(t("dhcp.warn_disable", net=iface))
        info.update(iface=iface, sid=p["id"] if p else iface, start=start, limit=limit, leasetime=leasetime, enabled=enabled)
        changes = [t("dhcp.do_pool", net=iface, first=str(rng[0]) if rng else "?", last=str(rng[1]) if rng else "?",
                     lt=leasetime) + ("" if enabled else " · " + t("dhcp.disabled"))]
    elif op in ("dom_add", "dom_edit"):
        name = (str(form.get("name") or "").strip()).rstrip(".")
        ip = str(form.get("ip") or "").strip()
        d = None
        if op == "dom_edit":
            d = next((x for x in m["domains"] if x["id"] == str(form.get("id") or "")), None)
            if not d:
                return result(request, title, actions.Result(False, t("msg.bad_request")))
        errs, warns = dnsmod.check_domain(m, name, ip, d["id"] if d else None, t)
        if errs:
            return result(request, title, actions.Result(False, "\n".join(errs)))
        ip = dnsmod.norm_addr(ip)
        cmds = dnsmod.domain_add_cmds(name, ip) if not d else dnsmod.domain_edit_cmds(d, name, ip)
        if not cmds:
            return result(request, title, actions.Result(False, t("msg.no_change")))
        info.update(name=name, ip=ip, id=d["id"] if d else "")
        changes = [t("dns.do_add", name=name, ip=ip) if not d else t("dns.do_edit", old=d["name"], name=name, ip=ip)]
    elif op in ("cname_add", "cname_edit"):
        cname = (str(form.get("cname") or "").strip()).rstrip(".")
        target = (str(form.get("target") or "").strip()).rstrip(".")
        c = None
        if op == "cname_edit":
            c = next((x for x in m["cnames"] if x["id"] == str(form.get("id") or "")), None)
            if not c:
                return result(request, title, actions.Result(False, t("msg.bad_request")))
        errs, warns = dnsmod.check_cname(m, cname, target, c["id"] if c else None, t)
        if errs:
            return result(request, title, actions.Result(False, "\n".join(errs)))
        cmds = dnsmod.cname_add_cmds(cname, target) if not c else dnsmod.cname_edit_cmds(c, cname, target)
        if not cmds:
            return result(request, title, actions.Result(False, t("msg.no_change")))
        info.update(cname=cname, target=target, id=c["id"] if c else "")
        changes = [t("dns.do_cname", cname=cname, target=target) if not c else
                   t("dns.do_cname_edit", old=c["cname"], cname=cname, target=target)]
    elif op in ("dom_del", "cname_del"):
        sid = str(form.get("id") or "")
        it = next((x for x in (m["domains"] if op == "dom_del" else m["cnames"]) if x["id"] == sid), None)
        if not it:
            return result(request, title, actions.Result(False, t("msg.bad_request")))
        cmds = dnsmod.del_cmds(sid)
        info.update(id=sid)
        changes = [t("dns.do_del", name=it["name"], ip=it["ip"]) if op == "dom_del" else
                   t("dns.do_cname_del", cname=it["cname"], target=it["target"])]
    elif op in ("dns_up", "fwd_add", "fwd_del", "wan_dns"):
        plan = await _upstream_plan(t, form, m, op)
        if isinstance(plan, str):
            return result(request, title, actions.Result(False, plan))
        cmds, shown, changes, warns, extra = plan
        info.update(extra)
        token = store.add_svc_pending(r.name, cmds, {**info, "changes": changes})
        return render(request, "_svc_preview.html", ap=r.name, cmds=shown, changes=changes, token=token,
                      note=t("dns.note_wan" if op == "wan_dns" else "dns.note_up"), warns=warns,
                      danger=op == "wan_dns")
    else:
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    token = store.add_svc_pending(r.name, cmds, {**info, "changes": changes})
    return render(request, "_svc_preview.html", ap=r.name, cmds=[*cmds, "uci commit dhcp", "/etc/init.d/dnsmasq reload"],
                  changes=changes, token=token, note=t("dns.note" if op[:3] in ("dom", "cna") else "dhcp.note"),
                  warns=warns)


def _svc_plan(t, form) -> tuple[str, list[str], dict, list[str]] | str:
    """Z formulare akce -> (zarizeni, prikazy, info pro log/demo, popis zmeny) nebo chybovy klic."""
    ap, op = str(form.get("ap") or ""), str(form.get("op") or "")
    sv = store.svc.get(ap)
    if not sv:
        return "msg.bad_request"
    info: dict = {"op": op}
    if op == "svc":
        svc = svcmod.valid_service(str(form.get("name") or ""), sv.get("services") or [])
        action = str(form.get("action") or "")
        cmds = svcmod.svc_cmds(svc, action) if svc else None
        if not cmds:
            return "svc.err_action"
        info.update(name=svc["name"], action=action)
        return ap, cmds, info, [t("svc.do_" + action, name=svc["name"])]
    adb, ban = sv.get("adblock"), sv.get("banip")
    if op.startswith("adb_") and not adb or op.startswith("ban_") and not ban:
        return "msg.bad_request"
    if op == "adb_suspend":
        try:
            m = max(0, min(int(form.get("minutes") or 0), 7 * 24 * 60))
        except ValueError:
            return "msg.bad_request"
        info["minutes"] = m
        return ap, svcmod.adb_suspend_cmds(m), info, [t("adb.do_suspend", m=m) if m else t("adb.do_suspend_forever")]
    if op == "adb_resume":
        return ap, svcmod.adb_resume_cmds(), info, [t("adb.do_resume")]
    if op == "adb_reload":
        return ap, svcmod.adb_reload_cmds(), info, [t("adb.do_reload")]
    if op == "ban_reload":
        return ap, ["/etc/init.d/banip reload"], info, [t("ban.do_reload")]
    if op in ("adb_list", "ban_list"):
        kind = "adblock" if op == "adb_list" else "banip"
        target, action = str(form.get("target") or ""), str(form.get("action") or "add")
        raw = str(form.get("value") or "")
        value = svcmod.norm_domain(raw) if kind == "adblock" else svcmod.norm_ip(raw)
        if not value:
            return "adb.err_domain" if kind == "adblock" else "ban.err_ip"
        cmds = svcmod.list_cmds(kind, target, action, value, reload=kind == "adblock" or bool(ban and ban["enabled"]))
        if not cmds:
            return "msg.bad_request"
        info.update(target=target, action=action, value=value)
        return ap, cmds, info, [t(f"list.{action}_{target}", v=value)]
    return "msg.bad_request"


@app.post("/services/preview", response_class=HTMLResponse)
async def services_preview(request: Request):
    """Nahled akce na sluzbe / adblocku / banIP – prikazy se ukazou, provedou se az po Pouzit."""
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    form = await request.form()
    plan = _svc_plan(t, form)
    if isinstance(plan, str):
        return result(request, title, actions.Result(False, t(plan)))
    ap, cmds, info, changes = plan
    token = store.add_svc_pending(ap, cmds, {**info, "changes": changes})
    note = t("svc.note_" + info["op"]) if info["op"] in ("adb_suspend", "adb_list", "ban_list", "svc") else ""
    if info["op"] == "svc" and (store.svc.get(ap) or {}).get("services") and \
            svcmod.valid_service(info["name"], store.svc[ap]["services"]).get("protected"):
        note = t("svc.note_protected")
    return render(request, "_svc_preview.html", ap=ap, cmds=cmds, changes=changes, token=token, note=note)


@app.post("/services/apply", response_class=HTMLResponse)
async def services_apply(request: Request, token: str = Form(...)):
    require_htmx(request)
    t = request.state.t
    title = t("preview.apply_title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    entry = store.svc_pending.pop(token, None)
    if not entry or time.time() - entry[0] > 600:
        return result(request, title, actions.Result(False, t("msg.preview_expired")))
    _, ap, cmds, info = entry
    summary = "; ".join(info.get("changes") or [])
    log.info("%s: %s na %s", who(request), summary, ap)
    if store.opts.get("demo"):
        await asyncio.sleep(0.5)
        if info.get("op") == "wan_dns":
            store.dhcp_demo_wan = list(info["servers"])
        elif info.get("op") == "fw_toggle":
            store.fw_demo[info["id"]] = info["enable"]
        elif info.get("uci") == "dhcp":
            dhcpmod.demo_apply(store.dhcp_demo, info)
        else:
            store.demo_svc_apply(ap, info)
        r = actions.Result(True, f"{ap}: OK (demo)")
    elif info.get("uci") == "dhcp":
        r = await dhcp_apply(ap, cmds, summary, t, info.get("script"))
    elif info.get("uci") == "firewall":
        r = await safe_apply(ap, ["firewall"], cmds, fwmod.RELOAD, summary, t, check=fwmod.CHECK)
    else:
        lock = store.ap_lock(ap)
        if lock.locked():
            return result(request, title, actions.Result(False, t("msg.busy")))
        async with lock:
            r = await actions.run(store.cfg(ap), store.key, " && ".join(cmds) + " && echo OK", timeout=90, t=t,
                                  summary=f"{summary} ({ap})")
    if info.get("uci") == "firewall":
        await store.load_fw(force=True)
        resp = result(request, title, r)
        if r.ok:
            resp.headers["HX-Refresh"] = "true"
        return resp
    if info.get("uci") == "dhcp":
        await store.load_dhcp(force=True)
        if r.ok and not store.opts.get("demo"):
            async def repoll():  # jmena a rezervace i na ostatnich strankach
                await asyncio.sleep(3)
                await store.poll()
            asyncio.create_task(repoll())
        resp = result(request, title, r)
        if r.ok:
            resp.headers["HX-Refresh"] = "true"
        return resp
    await store.load_services(only=ap, force=True)
    resp = result(request, title, r)
    if r.ok:
        resp.headers["HX-Refresh"] = "true"
    return resp


@app.post("/services/search", response_class=HTMLResponse)
async def services_search(request: Request, ap: str = Form(""), kind: str = Form(""), value: str = Form("")):
    """Proc je domena / IP blokovana – jen cteni, smi i uzivatel s pristupem jen pro cteni."""
    require_htmx(request)
    t = request.state.t
    if ap not in store.svc and any(d.name == ap for d in store.svc_devices()):
        await store.load_services(only=ap)
    sv = store.svc.get(ap) or {}
    data = sv.get(kind) if kind in ("adblock", "banip") else None
    if not data:
        return result(request, t("svc.search"), actions.Result(False, t("msg.bad_request")))
    v = svcmod.norm_domain(value) if kind == "adblock" else svcmod.norm_ip(value)
    if not v or (kind == "banip" and "/" in v):
        return result(request, t("svc.search"), actions.Result(False, t("adb.err_domain" if kind == "adblock" else "ban.err_ip")))
    title = t("svc.search_title", v=v)
    if store.opts.get("demo"):
        return result(request, title, actions.Result(True, svcmod.demo_search(kind, v, data)))
    r = await actions.run(store.cfg(ap), store.key, svcmod.search_cmd(kind, v, data.get("searchcmd", "search")),
                          timeout=60, t=t, audit=False)
    return result(request, title, r)


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    refresh, _ = refresh_interval(request)
    return render(request, "stats.html", page="stats", s=store.stats_view(),
                  refresh=max(5, store.stats_interval) if refresh else 0)


@app.get("/api/stats")
async def api_stats():
    v = store.stats_view()
    for i in v["ifaces"]:
        i.pop("spark", None)
    for p in v["ports"]:
        p.pop("spark", None)
    v.pop("temp_spark", None)
    v["pings"] = {k: [{"name": p.name, "host": p.host, "ms": p.last, "ok": p.ok, "loss_1h": round(p.loss(), 2),
                       "avg_1h": p.avg()} for p in lst] for k, lst in v["pings"].items()}
    v["wifi"] = [{k: c.get(k) for k in ("mac", "hostname", "ip", "ap", "ssid", "down", "up")} for c in v["wifi"]]
    return v


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return render(request, "setup.html", page="setup")


@app.post("/refresh")
async def refresh(request: Request, back: str = "/"):
    # jen cteni (obnovi data), povoleno i roli read
    await store.poll()
    base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    return RedirectResponse(f"{base}{back}", status_code=303)


@app.get("/api/state")
async def api_state():
    return {
        "last_poll": store.last_poll,
        "aps": [{k: v for k, v in asdict(a).items() if k != "clients"} | {"client_count": a.client_count}
                for a in store.aps],
        "router": {k: v for k, v in asdict(store.router).items() if k not in ("clients", "radios", "ssids", "wifi_ifaces")}
                  if store.router else None,
        "clients": store.all_clients(),
        "leases": store.lease_list(),
    }


# ---------------------------------------------------------------- akce

def require_htmx(request: Request):
    """Akce jen z UI (htmx posila hlavicku, cizi formular ji nastavit nemuze -> CSRF)."""
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(400, request.state.t("msg.ui_only"))


def result(request: Request, title: str, r: actions.Result):
    return render(request, "_result.html", title=title, ok=r.ok, output=r.output)


async def ap_action(request: Request, name: str, title: str, fn, demo_text: str):
    require_htmx(request)
    t = request.state.t
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    ap = store.ap(name)
    lock = store.ap_lock(name)
    if lock.locked():
        return result(request, title, actions.Result(False, t("msg.busy")))
    async with lock:
        log.info("%s: %s %s", who(request), title, name)
        if store.opts.get("demo"):
            await asyncio.sleep(1)
            return result(request, title, actions.Result(True, demo_text))
        r = await fn(store.cfg(name), store.key, ap, t)
    log.info("%s: %s %s -> %s", who(request), title, name, "OK" if r.ok else "error")
    return result(request, title, r)


@app.post("/ap/{name}/reboot", response_class=HTMLResponse)
async def ap_reboot(request: Request, name: str):
    t = request.state.t

    async def fn(cfg, key, ap, t):
        r = await actions.reboot(cfg, key, t)
        if r.ok:
            ap.online, ap.error = False, t("ap.rebooting")
        return r
    return await ap_action(request, name, t("ap.reboot"), fn, t("msg.reboot_sent") + " (demo)")


@app.post("/ap/{name}/pkg-update", response_class=HTMLResponse)
async def ap_pkg_update(request: Request, name: str):
    t = request.state.t

    async def fn(cfg, key, ap, t):
        r = await actions.pkg_update(cfg, key, ap.pkg or "apk", t)
        if r.ok:  # vysledek rucni kontroly = i prehled aktualizaci
            store.updates[ap.name] = {"ts": time.time(), "pkgs": maint.parse_upgradable(r.output), "error": ""}
            store.save_updates()
        return r
    return await ap_action(request, name, t("ap.check_updates"), fn,
                           t("msg.upgradable_header") + "\nhostapd-utils-2025.08.26~1 < 2025.08.26~2\n"
                           "libubox20251010-2025.10.10~1 < 2025.10.10~2")


@app.post("/ap/{name}/pkg-upgrade", response_class=HTMLResponse)
async def ap_pkg_upgrade(request: Request, name: str):
    t = request.state.t

    async def fn(cfg, key, ap, t):
        r = await actions.pkg_upgrade(cfg, key, ap.pkg or "apk", t)
        asyncio.create_task(store.poll())  # overlay se zmenil

        async def recheck():
            await asyncio.sleep(5)
            await store.check_one(ap)
        asyncio.create_task(recheck())
        return r
    return await ap_action(request, name, t("ap.upgrade"), fn,
                           "(1/2) Upgrading hostapd-utils\n(2/2) Upgrading libubox20251010\nOK: 2 packages (demo)")


@app.post("/ap/{name}/backup", response_class=HTMLResponse)
async def ap_backup(request: Request, name: str):
    require_htmx(request)
    t = request.state.t
    title = t("backup.now")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    ap = store.ap(name)
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, title, actions.Result(True, t("backup.done", file=f"{maint.safe_name(name)}-demo.tar.gz") + " (demo)"))
    log.info("%s: zaloha %s", who(request), name)
    ok, msg = await store.backup_one(ap)
    if msg == "busy":
        return result(request, title, actions.Result(False, t("msg.busy")))
    return result(request, title, actions.Result(ok, t("backup.done", file=msg) if ok else msg))


@app.get("/backup/{name}/{file}")
async def backup_download(request: Request, name: str, file: str):
    """Stazeni zalohy – obsahuje hesla Wi-Fi a klice, proto jen plny pristup."""
    require_full(request)
    store.ap(name)  # 404 pro nezname zarizeni
    if not maint.FILE_RE.match(file):
        raise HTTPException(404)
    path = maint.backup_dir(BACKUPS, name) / file
    if not path.is_file():
        raise HTTPException(404)
    log.info("%s: stazena zaloha %s", who(request), file)
    return FileResponse(path, media_type="application/gzip", filename=file)


@app.post("/clients/kick", response_class=HTMLResponse)
async def client_kick(request: Request, ap: str = Form(...), ifname: str = Form(...), mac: str = Form(...),
                      ban: int = Form(0)):
    """Odpojit klienta z Wi-Fi; s blokaci na vsech AP, ktere vysilaji stejne SSID (jinak by se pripojil jinde)."""
    require_htmx(request)
    t = request.state.t
    mac = mac.strip().lower()
    title = t("kick.title", who=(store.info.get(mac) or {}).get("hostname") or mac)
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    dev = store.ap(ap)
    ssid = next((x["ssid"] for x in dev.ssids if x["ifname"] == ifname), None)
    if ssid is None or ban not in actions.BAN_CHOICES or not actions.MAC_RE.match(mac):
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    targets: dict[str, list[str]] = {ap: [ifname]}
    if ban:
        for a in store.aps:
            if a.online:
                for x in a.ssids:
                    if x["ssid"] == ssid and x["ifname"] not in targets.get(a.name, []):
                        targets.setdefault(a.name, []).append(x["ifname"])
    log.info("%s: odpojit %s z %s (ban %s s) na %s", who(request), mac, ssid, ban, targets)
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, title, actions.Result(True, "\n".join(f"{a} {i}: OK (demo)" for a, l in targets.items() for i in l)))
    res = await asyncio.gather(*(actions.kick(store.cfg(a), store.key, l, mac, ban, t) for a, l in targets.items()))
    ok = res[0].ok  # hlavni je AP, kde klient byl
    if ok:
        dev.clients = [c for c in dev.clients if c["mac"] != mac]

        async def repoll():
            await asyncio.sleep(5)
            await store.poll()
        asyncio.create_task(repoll())
    out = "\n".join(r.output for r in res)
    if ok and ban:
        store.ban_meta[mac] = {"until": time.time() + ban, "ssid": ssid}
        for a in store.aps:  # hned ukazat v seznamu blokaci (dalsi nacteni ho potvrdi)
            for i in targets.get(a.name, []):
                if not any(b["mac"] == mac and b["ifname"] == i for b in a.bans):
                    a.bans.append({"mac": mac, "ifname": i, "ssid": ssid})
        out += "\n\n" + t("kick.ban_note")
    return result(request, title, actions.Result(ok, out))


@app.post("/clients/unban", response_class=HTMLResponse)
async def client_unban(request: Request, mac: str = Form(...), ssid: str = Form(...)):
    """Zrusit blokaci: del_client s ban_time 0 na vsech AP, ktere vysilaji dane SSID."""
    require_htmx(request)
    t = request.state.t
    mac = mac.strip().lower()
    title = t("unban.title", who=(store.info.get(mac) or {}).get("hostname") or mac)
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    if not actions.MAC_RE.match(mac):
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    targets = {a.name: [x["ifname"] for x in a.ssids if x["ssid"] == ssid] for a in store.aps if a.online}
    targets = {k: v for k, v in targets.items() if v}
    if not targets:
        return result(request, title, actions.Result(False, t("msg.bad_request")))
    log.info("%s: zrusit blokaci %s na %s", who(request), mac, targets)
    store.ban_meta.pop(mac, None)
    for a in store.aps:
        a.bans = [b for b in a.bans if not (b["mac"] == mac and b["ssid"] == ssid)]
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, title, actions.Result(True, "\n".join(f"{a} {i}: OK (demo)" for a, l in targets.items() for i in l)))
    res = await asyncio.gather(*(actions.kick(store.cfg(a), store.key, l, mac, 0, t) for a, l in targets.items()))
    return result(request, title, actions.Result(all(r.ok for r in res), "\n".join(r.output for r in res)))


# ---------------------------------------------------------------- SSID

@app.get("/ssids", response_class=HTMLResponse)
async def ssids_page(request: Request):
    nets = sorted({i["name"] for a in store.aps for i in a.interfaces if not i.get("wan") and i.get("name") != "loopback"},
                  key=lambda n: (n != "lan", n))
    radios = [{"ap": a.name, "radio": r["name"], "band": r.get("band", ""), "up": r.get("up")}
              for a in store.aps if a.online for r in a.radios]
    return render(request, "ssids.html", page="ssids", groups=store.ssid_groups(), autorefresh=False,
                  new_nets=nets or ["lan"], new_radios=radios, encryptions=actions.ENCRYPTIONS)


def _tri(v: str) -> bool | None:
    return {"1": True, "0": False}.get(v)


@app.post("/ssids/preview", response_class=HTMLResponse)
async def ssids_preview(request: Request, ssid: str = Form(...), new_ssid: str = Form(""),
                        new_key: str = Form(""), hidden: str = Form(""), disabled: str = Form("")):
    require_htmx(request)
    t = request.state.t
    title = t("preview.title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    form = await request.form()
    targets = form.getlist("target")  # "ap|section"
    group = next((g for g in store.ssid_groups() if g["ssid"] == ssid), None)
    if not group:
        return result(request, title, actions.Result(False, t("msg.ssid_not_found")))
    known = {(i["ap"], i["section"]): i for i in group["items"]}
    plan: dict[str, list[actions.SsidChange]] = {}
    rows = []
    for target in targets:
        ap_name, _, section = target.partition("|")
        item = known.get((ap_name, section))
        if not item:
            continue
        ch = actions.SsidChange(
            section=section,
            ssid=new_ssid if new_ssid and new_ssid != item["ssid"] else None,
            key=new_key or None,
            hidden=_tri(hidden) if _tri(hidden) is not None and _tri(hidden) != item["hidden"] else None,
            disabled=_tri(disabled) if _tri(disabled) is not None and _tri(disabled) != item["disabled"] else None,
        )
        err = actions.validate(ch, t)
        if err:
            return result(request, title, actions.Result(False, err))
        cmds = actions.uci_commands(ch)
        if cmds:
            plan.setdefault(ap_name, []).append(ch)
            rows.append({"ap": ap_name, "item": item, "ch": ch, "cmds": [actions.mask(c) for c in cmds]})
    if not rows:
        return result(request, title, actions.Result(False, t("msg.no_change")))
    # cekajici zmena na serveru, heslo se do stranky nevraci
    now = time.time()
    store.pending = {k: v for k, v in store.pending.items() if now - v[0] < 600}
    token = secrets.token_urlsafe(16)
    store.pending[token] = (now, plan)
    return render(request, "_ssid_preview.html", rows=rows, token=token)


@app.post("/ssids/apply", response_class=HTMLResponse)
async def ssids_apply(request: Request, token: str = Form(...)):
    require_htmx(request)
    t = request.state.t
    title = t("preview.apply_title")
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, t("msg.no_permission")))
    entry = store.pending.pop(token, None)
    if not entry or time.time() - entry[0] > 600:
        return result(request, title, actions.Result(False, t("msg.preview_expired")))
    plan = entry[1]
    log.info("%s: SSID change on %s", who(request), ", ".join(plan))
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, title, actions.Result(True, "\n".join(f"{ap}: OK (demo)" for ap in plan)))

    async def one(ap_name, changes):
        async with store.ap_lock(ap_name):
            r = await actions.apply_ssid(store.cfg(ap_name), store.key, changes, t)
        return f"== {ap_name} ==\n{r.output}", r.ok

    res = await asyncio.gather(*(one(a, c) for a, c in plan.items()))

    async def repoll():
        await asyncio.sleep(8)  # wifi reload chvili trva
        await store.poll()
    asyncio.create_task(repoll())
    return result(request, title, actions.Result(all(ok for _, ok in res), "\n\n".join(o for o, _ in res)))
