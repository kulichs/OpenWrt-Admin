"""Jmena zarizeni z Home Assistantu podle MAC (registr zarizeni).

Add-on ma v config.yaml `homeassistant_api: true`, Supervisor mu da SUPERVISOR_TOKEN a pusti ho
na websocket HA (ws://supervisor/core/websocket). `config/device_registry/list` vrati zarizeni
s `connections` [["mac", "aa:bb:..."]] – typicky ESPHome, Shelly, Tasmota, chytre TV...
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger("openwrt_admin.ha")

WS_URL = os.environ.get("OWADMIN_HA_WS", "ws://supervisor/core/websocket")


def _norm_mac(m: str) -> str:
    m = (m or "").strip().lower().replace("-", ":")
    if len(m) == 12 and ":" not in m:
        m = ":".join(m[i:i + 2] for i in range(0, 12, 2))
    return m


def parse_devices(devices: list[dict]) -> dict[str, str]:
    """Registr zarizeni -> {mac: jmeno}; prednost ma jmeno nastavene uzivatelem."""
    out = {}
    for d in devices or []:
        name = d.get("name_by_user") or d.get("name") or ""
        if not name:
            continue
        for conn in d.get("connections") or []:
            if len(conn) == 2 and conn[0] == "mac":
                mac = _norm_mac(conn[1])
                if len(mac) == 17:
                    out[mac] = name
    return out


async def _session(requests: list[dict], timeout: float = 15.0) -> list[dict]:
    """Jedno websocket spojeni s HA: prihlaseni a postupne odeslani pozadavku; vrati odpovedi ve stejnem poradi.
    Bez SUPERVISOR_TOKEN (lokalni vyvoj) vyhodi RuntimeError."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("no SUPERVISOR_TOKEN")
    import websockets  # az tady: lokalne bez HA neni potreba

    async def run():
        async with websockets.connect(WS_URL, max_size=32 * 2**20, open_timeout=timeout) as ws:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "auth_required":
                await ws.send(json.dumps({"type": "auth", "access_token": token}))
                msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"HA auth: {msg.get('type')} {msg.get('message', '')}")
            out = []
            for n, req in enumerate(requests, start=1):
                await ws.send(json.dumps({"id": n, **req}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        break
                if not msg.get("success"):
                    raise RuntimeError(f"HA {req.get('type')}: {msg.get('error')}")
                out.append(msg.get("result"))
            return out

    return await asyncio.wait_for(run(), timeout * 2)


async def fetch_names(timeout: float = 15.0) -> dict[str, str]:
    """Nacte jmena z HA. Bez SUPERVISOR_TOKEN (lokalni vyvoj) vrati {}. Chyby vyhazuje dal."""
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {}
    (devices,) = await _session([{"type": "config/device_registry/list"}], timeout)
    return parse_devices(devices or [])


def parse_users(users: list[dict]) -> dict[str, dict]:
    """config/auth/list -> {id: {username, name, admin}}; systemove ucty (Supervisor, aplikace) vynechat."""
    out = {}
    for u in users or []:
        if not u.get("id") or u.get("system_generated"):
            continue
        out[u["id"]] = {"username": (u.get("username") or "").lower(), "name": u.get("name") or "",
                        "admin": bool(u.get("is_owner")) or "system-admin" in (u.get("group_ids") or []),
                        "active": u.get("is_active", True)}
    return out


async def fetch_users(timeout: float = 15.0) -> dict[str, dict] | None:
    """Uzivatele HA (jen admin smi `config/auth/list`; aplikace jde pres Supervisor). Bez SUPERVISOR_TOKEN None."""
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return None
    (users,) = await _session([{"type": "config/auth/list"}], timeout)
    return parse_users(users or [])


async def ha_language() -> str:
    """Jazyk Home Assistantu (get_config -> language), napr. 'cs'. Pri chybe ''."""
    try:
        (cfg,) = await _session([{"type": "get_config"}])
        return str((cfg or {}).get("language") or "")
    except Exception:  # noqa: BLE001
        return ""


MD_SPECIAL = "\\`*_[]()<>#|~!"


def md_escape(s) -> str:
    """Text ze site (jmena zarizeni, SSID) do Markdownu notifikace: zadne odkazy, obrazky ani formatovani."""
    return "".join("\\" + ch if ch in MD_SPECIAL else ch for ch in str(s or "")).replace("\n", " ")


def md_plain(s: str) -> str:
    """Markdown notifikace -> prosty text pro mobil (bez ** a `, bez escapovani)."""
    import re
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"\\(.)", r"\1", s)


def service_call(service: str, data: dict) -> dict | None:
    """'notify.mobile_app_x' -> websocket call_service; None pri spatnem tvaru."""
    domain, _, name = (service or "").strip().partition(".")
    if not domain or not name:
        return None
    return {"type": "call_service", "domain": domain, "service": name, "service_data": data}


async def notify(title: str, message: str, notification_id: str = "", service: str = "") -> None:
    """Trvala notifikace v HA (zvonecek) a volitelne i sluzba notify.* (napr. mobilni aplikace)."""
    reqs = [{"type": "call_service", "domain": "persistent_notification", "service": "create",
             "service_data": {"title": title, "message": message, **({"notification_id": notification_id} if notification_id else {})}}]
    plain = md_plain(message)  # mobilni notifikace markdown nezobrazi
    extra = service_call(service, {"title": title, "message": plain})
    if extra:
        reqs.append(extra)
    await _session(reqs)


API_URL = os.environ.get("OWADMIN_HA_API", "http://supervisor/core/api")


def set_states(entities: list[tuple[str, object, dict]], timeout: float = 10.0) -> int:
    """Zapise stavy entit pres REST API HA (POST /api/states/<entity_id>), jedno spojeni pro vsechny.
    Entity vznikaji samy pri prvnim zapisu (bez unique_id – nejdou upravovat v UI, ale maji historii
    a jdou do statistik, InfluxDB/Grafany). Blokujici – volat pres asyncio.to_thread. Vrati pocet zapsanych."""
    import http.client
    from urllib.parse import quote, urlsplit
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("no SUPERVISOR_TOKEN")
    u = urlsplit(API_URL)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port, timeout=timeout)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    n = 0
    try:
        for entity_id, state, attrs in entities:
            body = json.dumps({"state": "unknown" if state is None else state, "attributes": attrs})
            conn.request("POST", f"{u.path.rstrip('/')}/states/{quote(entity_id)}", body=body, headers=headers)
            r = conn.getresponse()
            r.read()
            if r.status not in (200, 201):
                raise RuntimeError(f"HA {entity_id}: HTTP {r.status}")
            n += 1
    finally:
        conn.close()
    return n
