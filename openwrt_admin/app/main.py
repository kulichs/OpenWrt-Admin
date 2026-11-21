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

from . import actions
from .collector import APConfig, APState, band_of, collect_ap, collect_leases, norm_band
from .demo import demo_states

DATA = Path(os.environ.get("OWADMIN_DATA", "/data"))
OPTIONS = DATA / "options.json"
KEYFILE = DATA / "id_ed25519"
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
    opts = {"aps": [], "ssh_user": "root", "poll_interval": 30, "dhcp_router": "", "demo": False,
            "full_access": [], "read_access": [], "refresh_interval": 30}
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

class Store:
    def __init__(self):
        self.opts = load_options()
        self.key = load_key()
        self.pubkey = self.key.export_public_key().decode().strip()
        self.aps: list[APState] = [APState(name=a["name"], host=a["host"]) for a in self.opts["aps"]]
        self.leases: dict[str, dict] = {}
        self.last_poll = 0.0
        self.lock = asyncio.Lock()
        self.ap_locks: dict[str, asyncio.Lock] = {}
        self.pending: dict[str, tuple[float, dict[str, list[actions.SsidChange]]]] = {}

    def ap(self, name: str) -> APState:
        ap = next((a for a in self.aps if a.name == name), None)
        if not ap:
            raise HTTPException(404, "AP nenalezen")
        return ap

    def cfg(self, name: str) -> APConfig:
        a = next((a for a in self.opts["aps"] if a["name"] == name), None)
        if not a:
            raise HTTPException(404, "AP nenalezen")
        return APConfig(name=a["name"], host=a["host"], port=int(a.get("port") or 22),
                        user=self.opts.get("ssh_user", "root"))

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
                self.last_poll = time.time()
                return
            user = self.opts.get("ssh_user", "root")
            cfgs = [APConfig(name=a["name"], host=a["host"], port=int(a.get("port") or 22), user=user)
                    for a in self.opts["aps"]]
            tasks = [collect_ap(c, self.key) for c in cfgs]
            router = self.opts.get("dhcp_router")
            if router:
                tasks.append(collect_leases(router, user, self.key))
            res = await asyncio.gather(*tasks)
            if router:
                self.leases = res.pop() or self.leases
            self.aps = list(res)
            self.last_poll = time.time()

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
             ", ".join(f"{a['name']}={a['host']}" for a in store.opts["aps"]) or "-",
             store.opts.get("dhcp_router") or "-", store.opts.get("demo"))
    task = asyncio.create_task(poller())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------------------------------------------------------------- uzivatel a role
# Ingress (Supervisor) posila identitu prihlaseneho uzivatele HA v hlavickach.
# Add-on je dostupny jen z Ingress IP, takze hlavicky nastavuje HA, ne prohlizec.
H_USER_ID, H_USER_NAME, H_USER_DISPLAY = "X-Remote-User-Id", "X-Remote-User-Name", "X-Remote-User-Display-Name"
ROLE_LABEL = {"full": "plný přístup", "read": "jen čtení", "none": "bez přístupu"}


def identify(request: Request) -> dict:
    h = request.headers
    user = {"id": h.get(H_USER_ID, ""), "name": h.get(H_USER_NAME, "") or os.environ.get("OWADMIN_DEV_USER", ""),
            "display": h.get(H_USER_DISPLAY, "")}
    full = {u.strip().lower() for u in store.opts.get("full_access") or [] if u.strip()}
    read = {u.strip().lower() for u in store.opts.get("read_access") or [] if u.strip()}
    names = {user["name"].lower(), user["display"].lower()} - {""}
    if not full and not read:
        role, why = "full", "role nejsou nastavené (full_access / read_access prázdné)"
    elif not names:
        role, why = "read", "Ingress neposlal jméno uživatele, bezpečný režim jen pro čtení"
    elif names & full:
        role, why = "full", "uživatel je ve full_access"
    elif names & read:
        role, why = "read", "uživatel je v read_access"
    else:
        role, why = "none", "uživatel není ve full_access ani read_access"
    return {**user, "role": role, "why": why, "label": ROLE_LABEL[role]}


@app.middleware("http")
async def ingress_only(request: Request, call_next):
    if not ALLOW_ALL and request.client and request.client.host not in INGRESS_IPS:
        return JSONResponse({"detail": "Pouze pres Home Assistant Ingress"}, status_code=403)
    if request.url.path.startswith("/static/") or store is None:
        return await call_next(request)
    user = identify(request)
    request.state.user = user
    if user["role"] == "none":
        log.warning("Odepreno: %s (%s) %s %s", user["name"] or "?", user["display"], request.method, request.url.path)
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        return templates.TemplateResponse(request, "denied.html", {"base": base, "user": user}, status_code=403)
    return await call_next(request)


def require_full(request: Request):
    if request.state.user["role"] != "full":
        raise HTTPException(403, "Na tuto akci nemáš oprávnění (jen čtení).")


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


def fmt_duration(s) -> str:
    if s is None:
        return "–"
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def fmt_rate(kbit) -> str:
    if not kbit:
        return "–"
    return f"{kbit / 1000:.0f} Mb/s"


def signal_class(sig) -> str:
    if sig is None:
        return ""
    return "good" if sig >= -60 else "ok" if sig >= -72 else "bad"


def ago(ts) -> str:
    if not ts:
        return "nikdy"
    s = int(time.time() - ts)
    return f"před {s} s" if s < 120 else f"před {s // 60} min"


templates.env.filters.update(bytes=fmt_bytes, duration=fmt_duration, rate=fmt_rate,
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
        "user": request.state.user, "can_edit": request.state.user["role"] == "full", **ctx})


# ---------------------------------------------------------------- stranky

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    clients = store.all_clients()
    return render(request, "dashboard.html", page="dashboard", clients=clients)


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
    clients = [c for c in store.all_clients() if c["ap"] == name]
    return render(request, "ap.html", page="ap:" + name, ap=ap, clients=clients)


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
        "clients": store.all_clients(),
    }


# ---------------------------------------------------------------- akce

def require_htmx(request: Request):
    """Akce jen z UI (htmx posila hlavicku, cizi formular ji nastavit nemuze -> CSRF)."""
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(400, "Akce jen z rozhraní add-onu")


def result(request: Request, title: str, r: actions.Result):
    return render(request, "_result.html", title=title, ok=r.ok, output=r.output)


async def ap_action(request: Request, name: str, title: str, fn, demo_text: str):
    require_htmx(request)
    if request.state.user["role"] != "full":
        return result(request, title, actions.Result(False, "Na tuto akci nemáš oprávnění (jen čtení)."))
    ap = store.ap(name)
    lock = store.ap_lock(name)
    if lock.locked():
        return result(request, title, actions.Result(False, "Na tomto AP už běží jiná akce, počkej na dokončení."))
    async with lock:
        log.info("%s: %s %s", who(request), title, name)
        if store.opts.get("demo"):
            await asyncio.sleep(1)
            return result(request, title, actions.Result(True, demo_text))
        r = await fn(store.cfg(name), store.key, ap)
    log.info("%s: %s %s -> %s", who(request), title, name, "OK" if r.ok else "chyba")
    return result(request, title, r)


@app.post("/ap/{name}/reboot", response_class=HTMLResponse)
async def ap_reboot(request: Request, name: str):
    async def fn(cfg, key, ap):
        r = await actions.reboot(cfg, key)
        if r.ok:
            ap.online, ap.error = False, "restartuje se"
        return r
    return await ap_action(request, name, "Restart", fn, "Restart odeslán (demo).")


@app.post("/ap/{name}/pkg-update", response_class=HTMLResponse)
async def ap_pkg_update(request: Request, name: str):
    async def fn(cfg, key, ap):
        return await actions.pkg_update(cfg, key, ap.pkg or "apk")
    return await ap_action(request, name, "Kontrola aktualizací", fn,
                           "== Dostupné aktualizace ==\nhostapd-utils-2025.08.26~1 < 2025.08.26~2\n"
                           "libubox20251010-2025.10.10~1 < 2025.10.10~2")


@app.post("/ap/{name}/pkg-upgrade", response_class=HTMLResponse)
async def ap_pkg_upgrade(request: Request, name: str):
    async def fn(cfg, key, ap):
        r = await actions.pkg_upgrade(cfg, key, ap.pkg or "apk")
        asyncio.create_task(store.poll())  # overlay se zmenil
        return r
    return await ap_action(request, name, "Upgrade balíčků", fn,
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
    if request.state.user["role"] != "full":
        return result(request, "Náhled", actions.Result(False, "Na změny SSID nemáš oprávnění (jen čtení)."))
    form = await request.form()
    targets = form.getlist("target")  # "ap|section"
    group = next((g for g in store.ssid_groups() if g["ssid"] == ssid), None)
    if not group:
        return result(request, "Náhled", actions.Result(False, "SSID nenalezeno, obnov stránku."))
    known = {(i["ap"], i["section"]): i for i in group["items"]}
    plan: dict[str, list[actions.SsidChange]] = {}
    rows = []
    for t in targets:
        ap_name, _, section = t.partition("|")
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
        err = actions.validate(ch)
        if err:
            return result(request, "Náhled", actions.Result(False, err))
        cmds = actions.uci_commands(ch)
        if cmds:
            plan.setdefault(ap_name, []).append(ch)
            rows.append({"ap": ap_name, "item": item, "ch": ch, "cmds": [actions.mask(c) for c in cmds]})
    if not rows:
        return result(request, "Náhled", actions.Result(False, "Žádná změna (nic nevybráno nebo hodnoty stejné)."))
    # cekajici zmena na serveru, heslo se do stranky nevraci
    now = time.time()
    store.pending = {k: v for k, v in store.pending.items() if now - v[0] < 600}
    token = secrets.token_urlsafe(16)
    store.pending[token] = (now, plan)
    return render(request, "_ssid_preview.html", rows=rows, token=token)


@app.post("/ssids/apply", response_class=HTMLResponse)
async def ssids_apply(request: Request, token: str = Form(...)):
    require_htmx(request)
    if request.state.user["role"] != "full":
        return result(request, "Použít", actions.Result(False, "Na změny SSID nemáš oprávnění (jen čtení)."))
    entry = store.pending.pop(token, None)
    if not entry or time.time() - entry[0] > 600:
        return result(request, "Použít", actions.Result(False, "Náhled vypršel, vytvoř ho znovu."))
    plan = entry[1]
    log.info("%s: změna SSID na %s", who(request), ", ".join(plan))
    if store.opts.get("demo"):
        await asyncio.sleep(1)
        return result(request, "Použít", actions.Result(True, "\n".join(f"{ap}: OK (demo)" for ap in plan)))

    async def one(ap_name, changes):
        async with store.ap_lock(ap_name):
            r = await actions.apply_ssid(store.cfg(ap_name), store.key, changes)
        return f"== {ap_name} ==\n{r.output}", r.ok

    res = await asyncio.gather(*(one(a, c) for a, c in plan.items()))

    async def repoll():
        await asyncio.sleep(8)  # wifi reload chvili trva
        await store.poll()
    asyncio.create_task(repoll())
    return result(request, "Použít", actions.Result(all(ok for _, ok in res), "\n\n".join(t for t, _ in res)))
