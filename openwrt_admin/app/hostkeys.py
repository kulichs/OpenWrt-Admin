"""Overovani klicu SSH serveru (TOFU – trust on first use).

Pri prvnim spojeni s hostitelem (host:port) se ulozi otisk jeho klice do /data/known_hosts.json. Pri dalsich
spojenich se otisk porovna; kdyz nesedi, spojeni se ZAVRE JESTE PRED PRVNIM PRIKAZEM (nikam neodejde heslo Wi-Fi ani
konfigurace) a zmena se ukaze v aplikaci s tlacitkem „Prijmout novy klic“ (legitimni po sysupgrade -n / reinstalaci).

Bez init() (testy, vyvoj) se nic nekontroluje.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("openwrt_admin.hostkeys")


class HostKeyChanged(OSError):
    """Otisk klice SSH serveru se lisi od ulozeneho – spojeni odmitnuto."""

    def __init__(self, hostport: str, old: str, new: str):
        super().__init__(f"SSH host key of {hostport} changed (stored {old}, now {new}) – connection refused")
        self.hostport, self.old, self.new = hostport, old, new


class Store:
    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        self.known: dict[str, dict] = {}
        self.changed: dict[str, dict] = {}       # hostport -> {old, new, type, ts} (ceka na rozhodnuti)
        if path:
            try:
                self.known = json.loads(path.read_text())
            except (OSError, ValueError):
                self.known = {}

    def _save(self):
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.known, indent=1))
        tmp.replace(self.path)

    def check(self, hostport: str, ktype: str, fp: str) -> str:
        """'ok' | 'new' (ulozeno) | 'changed' (odmitnout)."""
        with self.lock:
            k = self.known.get(hostport)
            if k is None:
                self.known[hostport] = {"fp": fp, "type": ktype, "first": time.time()}
                self._save()
                log.info("SSH: novy hostitel %s, ulozen otisk %s %s", hostport, ktype, fp)
                return "new"
            if k.get("fp") == fp:
                self.changed.pop(hostport, None)
                return "ok"
            if hostport not in self.changed or self.changed[hostport]["new"] != fp:
                log.warning("SSH: klic %s se zmenil (ulozeny %s, ted %s) – spojeni odmitnuto", hostport, k.get("fp"), fp)
            self.changed[hostport] = {"old": k.get("fp", ""), "old_type": k.get("type", ""), "new": fp, "type": ktype,
                                      "ts": time.time()}
            return "changed"

    def accept(self, hostport: str) -> bool:
        with self.lock:
            c = self.changed.pop(hostport, None)
            if not c:
                return False
            self.known[hostport] = {"fp": c["new"], "type": c["type"], "first": time.time(), "replaced": c["old"]}
            self._save()
            log.warning("SSH: novy klic %s prijat (%s, drive %s)", hostport, c["new"], c["old"])
            return True


STORE: Store | None = None


def init(path: Path | None) -> Store:
    global STORE
    STORE = Store(path)
    return STORE


def hostport(host: str, port: int) -> str:
    return f"{host}:{int(port or 22)}"


def verify(conn, host: str, port: int) -> None:
    """Hned po asyncssh.connect, pred prvnim prikazem. Pri zmene klice spojeni zavre a vyhodi HostKeyChanged."""
    if STORE is None:
        return
    key = conn.get_server_host_key()
    if key is None:
        return
    hp = hostport(host, port)
    if STORE.check(hp, key.get_algorithm(), key.get_fingerprint("sha256")) == "changed":
        c = STORE.changed[hp]
        conn.close()
        raise HostKeyChanged(hp, c["old"], c["new"])
