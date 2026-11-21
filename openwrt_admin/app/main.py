"""OpenWrt-Admin - Home Assistant add-on (jen cteni)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import asyncssh
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .collector import APConfig, APState, collect_ap, collect_leases
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
    opts = {"aps": [], "ssh_user": "root", "poll_interval": 30, "dhcp_router": "", "demo": False}
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


@app.middleware("http")
async def ingress_only(request: Request, call_next):
    if not ALLOW_ALL and request.client and request.client.host not in INGRESS_IPS:
        return JSONResponse({"detail": "Pouze pres Home Assistant Ingress"}, status_code=403)
    return await call_next(request)


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


def render(request: Request, name: str, **ctx):
    base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    return templates.TemplateResponse(request, name, {
        "base": base, "store": store, "now": time.time(), **ctx})


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
    ap = next((a for a in store.aps if a.name == name), None)
    if not ap:
        raise HTTPException(404, "AP nenalezen")
    clients = [c for c in store.all_clients() if c["ap"] == name]
    return render(request, "ap.html", page="ap", ap=ap, clients=clients)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return render(request, "setup.html", page="setup")


@app.post("/refresh")
async def refresh(request: Request, back: str = "/"):
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
