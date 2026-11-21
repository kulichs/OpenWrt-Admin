"""OpenWrt-Admin - Home Assistant add-on."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import asyncssh
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import actions, ha, i18n, maint, topology, wired as wiredmod
from .collector import APConfig, APState, band_of, collect_ap, collect_router, norm_band, parse_host_port
from .demo import demo_router, demo_states

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

def load_options() -> dict:
    opts = {"aps": [], "ssh_user": "root", "poll_interval": 30, "dhcp_router": "", "router_name": "Router", "demo": False,
            "full_access": [], "read_access": [], "refresh_interval": 30, "language": "auto",
            "ha_names": True, "names": [], "notify_new_devices": True, "notify_service": "", "notify_random_mac": True,
            "update_check_hours": 12, "backup_interval_days": 7, "backup_keep": 10}
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
        return APConfig(name=item["name"], host=item["host"], port=int(item.get("port") or 22),
                        user=self.opts.get("ssh_user", "root"))

    def router_cfg(self) -> APConfig | None:
        return self._cfg(self.router_item) if self.router_item else None

    def ap(self, name: str) -> APState:
        """AP nebo router podle jmena (vse, co ma stranku a akce)."""
        for d in [*self.aps, self.router]:
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
                self.seen = {"02:00:00:00:10:04": {"ap": "AP-Office", "port": "4", "ts": time.time() - 3 * 3600,
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
                self.wired_state = wiredmod.locate(self.aps, self.router, self.info, self.seen, known=self._known())
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
            if rc:
                tasks.append(collect_router(rc, self.key, wake=self.wake_ips()))
            res = await asyncio.gather(*tasks)
            if rc:
                self.router, leases = res.pop()
                self.leases = leases or self.leases
            self.aps = list(res)
            await self.refresh_ha()
            self.info = self.build_info()
            self.wired_state = wiredmod.locate(self.aps, self.router, self.info, self.seen, known=self._known())
            self._save_seen()
            self.last_poll = time.time()
        await self.track_new()

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
        if not self.router or not self.router.online or not any(a.arl for a in self.aps) or self.wired_state is None:
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

    def switch_diag(self, ap) -> dict | None:
        """Diagnostika switche AP pro stranku AP: porty, MAC, uplink/CPU, pamet 'naposledy videno'."""
        if not ap.online:
            return None
        info = self.info or {}
        a = wiredmod.analyze(ap, self.router)
        wifi = {c["mac"] for x in self.aps for c in x.clients}
        ap_macs = {x.mac: x.name for x in self.aps if x.mac}
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
        out = [{"name": a.name, "online": a.online} for a in self.aps]
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
            self.wired_state = wiredmod.locate(self.aps, self.router, self.info, self.seen, known=self._known())
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
                out.append({**c, "ip": e.get("ip", ""), "hostname": e.get("hostname", ""), "src": e.get("src", ""),
                            "first_seen": self.known.first_seen(c["mac"])})
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store()
    log.info("AP: %s, router: %s, demo: %s",
             ", ".join(f"{a['name']}={a['host']}" for a in store.ap_items) or "-",
             (f"{store.router_item['name']}={store.router_item['host']}" if store.router_item else "-"),
             store.opts.get("demo"))
    tasks = [asyncio.create_task(poller()), asyncio.create_task(maintainer())]
    yield
    for task in tasks:
        task.cancel()


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
templates.env.filters["left"] = lambda ts: max(0, int(ts - time.time()))  # zbyva sekund
templates.env.tests["recent"] = lambda ts, hours=24: bool(ts) and time.time() - ts < hours * 3600


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
        "base": base, "store": store, "now": time.time(), "autorefresh": True,
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
        return render(request, "router.html", page="ap:" + name, ap=ap, leases=leases, iface_counts=counts)
    clients = [c for c in store.all_clients() if c["ap"] == name]
    return render(request, "ap.html", page="ap:" + name, ap=ap, clients=clients, diag=store.switch_diag(ap))


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, clients: str = "1", empty: str = "0"):
    show_clients, show_empty = clients != "0", empty == "1"
    w = store.wired()
    router_wired = [l for l in store.lease_list(w)
                    if l["online"] and not l["ap"] and not l["cable_ap"] and not l["is_ap"]
                    and (l["known_net"] or not l["ip"] or not store.router)]  # IP mimo LAN/VLAN routeru (WAN) ne
    topo = topology.build(store.router, store.aps, router_wired, request.state.t, clients=store.all_clients(),
                          show_clients=show_clients, show_empty=show_empty,
                          ap_wired=w["by_ap"], parent=w["parent"])
    return render(request, "map.html", page="map", topo=topo, show_clients=show_clients, show_empty=show_empty)


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
    return render(request, "ssids.html", page="ssids", groups=store.ssid_groups(), autorefresh=False)


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
