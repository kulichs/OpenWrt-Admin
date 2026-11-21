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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from . import actions, i18n, topology, wired as wiredmod
from .collector import APConfig, APState, band_of, collect_ap, collect_router, norm_band, parse_host_port
from .demo import demo_router, demo_states

DATA = Path(os.environ.get("OWADMIN_DATA", "/data"))
OPTIONS = DATA / "options.json"
KEYFILE = DATA / "id_ed25519"
SEENFILE = DATA / "wired_seen.json"   # posledni zname misto kabelovych zarizeni (AP + port)
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
            "full_access": [], "read_access": [], "refresh_interval": 30, "language": "auto"}
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
        self.lock = asyncio.Lock()
        self.ap_locks: dict[str, asyncio.Lock] = {}
        self.pending: dict[str, tuple[float, dict[str, list[actions.SsidChange]]]] = {}

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
                self.wired_state = wiredmod.locate(self.aps, self.router, self.leases, self.seen)
                self.last_poll = time.time()
                return
            user = self.opts.get("ssh_user", "root")
            cfgs = [self._cfg(a) for a in self.ap_items]
            tasks = [collect_ap(c, self.key) for c in cfgs]
            rc = self.router_cfg()
            if rc:
                tasks.append(collect_router(rc, self.key))
            res = await asyncio.gather(*tasks)
            if rc:
                self.router, leases = res.pop()
                self.leases = leases or self.leases
            self.aps = list(res)
            self.wired_state = wiredmod.locate(self.aps, self.router, self.leases, self.seen)
            self._save_seen()
            self.last_poll = time.time()

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

    def wired(self) -> dict:
        if self.wired_state is None:
            self.wired_state = wiredmod.locate(self.aps, self.router, self.leases, self.seen)
        return self.wired_state

    def lease_list(self, wired: dict | None = None) -> list[dict]:
        """DHCP leases z routeru, u Wi-Fi klientu doplneno AP/SSID/pasmo, u kabelovych AP a port."""
        wifi = {c["mac"]: c for a in self.aps for c in a.clients}
        wired = wired or self.wired()
        cable = {d["mac"]: (ap, d["port"], d.get("stale")) for ap, devs in wired["by_ap"].items() for d in devs}
        out = []
        for mac, l in self.leases.items():
            c = wifi.get(mac)
            cab = cable.get(mac)
            out.append({"mac": mac, "ip": l.get("ip", ""), "hostname": l.get("hostname", ""),
                        "ap": c["ap"] if c else "", "ssid": c["ssid"] if c else "", "band": c.get("band", "") if c else "",
                        "cable_ap": cab[0] if cab else "", "cable_port": cab[1] if cab else "",
                        "cable_stale": cab[2] if cab else None,
                        "is_ap": mac in wired["ap_macs"]})

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
                l = self.leases.get(c["mac"], {})
                out.append({**c, "ip": l.get("ip", ""), "hostname": l.get("hostname", "")})
        return sorted(out, key=lambda c: (c["hostname"] or "~" + c["mac"]).lower())


store: Store | None = None


async def poller():
    while True:
        try:
            await store.poll()
        except Exception:  # noqa: BLE001 - poller nesmi umrit
            log.exception("Chyba pri sberu dat")
        await asyncio.sleep(max(10, int(store.opts.get("poll_interval", 30))))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store()
    log.info("AP: %s, router: %s, demo: %s",
             ", ".join(f"{a['name']}={a['host']}" for a in store.ap_items) or "-",
             (f"{store.router_item['name']}={store.router_item['host']}" if store.router_item else "-"),
             store.opts.get("demo"))
    task = asyncio.create_task(poller())
    yield
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
        return render(request, "router.html", page="ap:" + name, ap=ap, leases=store.lease_list())
    clients = [c for c in store.all_clients() if c["ap"] == name]
    return render(request, "ap.html", page="ap:" + name, ap=ap, clients=clients)


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, clients: str = "1", empty: str = "0"):
    show_clients, show_empty = clients != "0", empty == "1"
    w = store.wired()
    router_wired = [l for l in store.lease_list(w) if not l["ap"] and not l["cable_ap"] and not l["is_ap"]]
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
        return await actions.pkg_update(cfg, key, ap.pkg or "apk", t)
    return await ap_action(request, name, t("ap.check_updates"), fn,
                           t("msg.upgradable_header") + "\nhostapd-utils-2025.08.26~1 < 2025.08.26~2\n"
                           "libubox20251010-2025.10.10~1 < 2025.10.10~2")


@app.post("/ap/{name}/pkg-upgrade", response_class=HTMLResponse)
async def ap_pkg_upgrade(request: Request, name: str):
    t = request.state.t

    async def fn(cfg, key, ap, t):
        r = await actions.pkg_upgrade(cfg, key, ap.pkg or "apk", t)
        asyncio.create_task(store.poll())  # overlay se zmenil
        return r
    return await ap_action(request, name, t("ap.upgrade"), fn,
                           "(1/2) Upgrading hostapd-utils\n(2/2) Upgrading libubox20251010\nOK: 2 packages (demo)")


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
