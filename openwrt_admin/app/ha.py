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


async def fetch_names(timeout: float = 15.0) -> dict[str, str]:
    """Nacte jmena z HA. Bez SUPERVISOR_TOKEN (lokalni vyvoj) vrati {}. Chyby vyhazuje dal."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {}
    import websockets  # az tady: lokalne bez HA neni potreba

    async def run():
        async with websockets.connect(WS_URL, max_size=32 * 2**20, open_timeout=timeout) as ws:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "auth_required":
                await ws.send(json.dumps({"type": "auth", "access_token": token}))
                msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"HA auth: {msg.get('type')} {msg.get('message', '')}")
            await ws.send(json.dumps({"id": 1, "type": "config/device_registry/list"}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    break
            if not msg.get("success"):
                raise RuntimeError(f"HA device_registry: {msg.get('error')}")
            return parse_devices(msg.get("result") or [])

    return await asyncio.wait_for(run(), timeout * 2)
