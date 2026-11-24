"""Udrzba: prehled aktualizaci (balicky + nova verze OpenWrt), zalohy konfigurace, nova zarizeni v siti.

- Aktualizace: jednou za `update_check_hours` se na kazdem zarizeni pusti `apk update` / `opkg update`
  a seznam balicku k aktualizaci. Nova verze OpenWrt z https://downloads.openwrt.org/.versions.json.
- Zalohy: `sysupgrade -b` na zarizeni, archiv se prenese pres SSH (stdout) do /data/backups/<zarizeni>/.
  /data je soucasti zaloh Home Assistantu. Archiv obsahuje hesla Wi-Fi a klice -> stahovat jen plny pristup.
- Nova zarizeni: MAC, ktere add-on jeste nikdy nevidel, -> notifikace v HA (persistent_notification, volitelne notify.*).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

import asyncssh

from .collector import APConfig

log = logging.getLogger("openwrt_admin.maint")

# ---------------------------------------------------------------- aktualizace


def check_cmd(pkg: str) -> str:
    """Tichy dotaz na balicky k aktualizaci (bez vypisu stahovani)."""
    if pkg == "opkg":
        return "opkg update >/dev/null 2>&1; opkg list-upgradable 2>/dev/null"
    return "apk update -q >/dev/null 2>&1; apk list --upgradable 2>/dev/null"


APK_RE = re.compile(r"^(\S+)\s.*upgradable from:\s*(\S+?)\]?\s*$")
OPKG_RE = re.compile(r"^(\S+) - (\S+) - (\S+)\s*$")


def parse_upgradable(text: str) -> list[dict]:
    """Vystup `apk list --upgradable` nebo `opkg list-upgradable` -> [{name, old, new}]."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = OPKG_RE.match(line)
        if m:
            out.append({"name": m.group(1), "old": m.group(2), "new": m.group(3)})
            continue
        m = APK_RE.match(line)
        if m:  # apk: "hostapd-utils-2025.08.26~2 aarch64 {hostapd} (BSD) [upgradable from: hostapd-utils-2025.08.26~1]"
            new_full, old_full = m.group(1), m.group(2)
            name, new, old = _split_apk(new_full), "", ""
            if name:
                new = new_full[len(name) + 1:]
                old = old_full[len(name) + 1:] if old_full.startswith(name + "-") else old_full
            out.append({"name": name or new_full, "old": old, "new": new})
    return sorted(out, key=lambda p: p["name"])


def _split_apk(full: str) -> str:
    """'libubox20251010-2025.10.10~2' -> 'libubox20251010' (verze zacina cislici za poslednim '-<cislice>')."""
    m = re.match(r"^(.+?)-(\d[^-]*(?:-r\d+)?)$", full)
    return m.group(1) if m else ""


VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
VERSIONS_URL = "https://downloads.openwrt.org/.versions.json"


def version_of(release: str) -> tuple[int, int, int] | None:
    """'OpenWrt 25.12.2 r30000-…' -> (25, 12, 2); SNAPSHOT a nezname -> None."""
    m = VERSION_RE.search(release or "")
    return tuple(int(x) for x in m.groups()) if m else None


def newer_release(release: str, versions: dict) -> str:
    """Nejnovejsi stabilni verze OpenWrt, ktera je novejsi nez ta na zarizeni (prednost ma stejna rada), jinak ''."""
    cur = version_of(release)
    if not cur:
        return ""
    cands = [v for v in (version_of(versions.get("stable_version", "")), version_of(versions.get("oldstable_version", ""))) if v]
    same = [v for v in cands if v[:2] == cur[:2] and v > cur]
    if same:
        return "%d.%d.%d" % max(same)
    newer = [v for v in cands if v > cur]
    return "%d.%d.%d" % max(newer) if newer else ""


def fetch_versions(timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(VERSIONS_URL, timeout=timeout) as r:  # noqa: S310 - pevna https adresa
        return json.loads(r.read().decode())


async def check_updates(cfg: APConfig, key, pkg: str, timeout: float = 180.0) -> dict:
    """-> {"ts", "pkgs": [...], "error"}"""
    res = {"ts": time.time(), "pkgs": [], "error": ""}
    try:
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[key],
                                    known_hosts=None, connect_timeout=10) as conn:
            r = await asyncio.wait_for(conn.run(check_cmd(pkg), check=False), timeout)
            out = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode(errors="replace")
            res["pkgs"] = parse_upgradable(out)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        res["error"] = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    return res


# ---------------------------------------------------------------- zalohy

BACKUP_CMD = ("F=/tmp/owadmin-backup.tar.gz; rm -f $F; sysupgrade -b $F >/dev/null 2>&1 && cat $F; "
              "rc=$?; rm -f $F; exit $rc")
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.tar\.gz$")


def safe_name(name: str) -> str:
    return SAFE_RE.sub("_", name).strip("._") or "device"


def backup_dir(root: Path, device: str) -> Path:
    return root / safe_name(device)


def list_backups(root: Path, device: str) -> list[dict]:
    d = backup_dir(root, device)
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("*.tar.gz"):
        st = f.stat()
        out.append({"file": f.name, "size": st.st_size, "ts": st.st_mtime})
    return sorted(out, key=lambda b: -b["ts"])


def prune(root: Path, device: str, keep: int) -> None:
    for b in list_backups(root, device)[max(1, keep):]:
        try:
            (backup_dir(root, device) / b["file"]).unlink()
        except OSError:
            pass


async def backup(cfg: APConfig, key, root: Path, device: str, keep: int, timeout: float = 120.0) -> tuple[bool, str]:
    """sysupgrade -b na zarizeni -> /data/backups/<zarizeni>/<zarizeni>-YYYY-mm-dd_HHMMSS.tar.gz. -> (ok, soubor nebo chyba)"""
    try:
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[key],
                                    known_hosts=None, connect_timeout=10) as conn:
            from .actions import audit_prefix  # zaznam do syslogu, kdyz zalohu spustil uzivatel (Zalohovat ted)
            r = await asyncio.wait_for(conn.run(audit_prefix("sysupgrade -b (backup)") + BACKUP_CMD, check=False,
                                                encoding=None), timeout)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        return False, f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    data = r.stdout or b""
    if r.exit_status != 0 or data[:2] != b"\x1f\x8b":
        err = (r.stderr or b"").decode(errors="replace").strip()
        return False, err or f"sysupgrade -b: exit {r.exit_status}"
    d = backup_dir(root, device)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{safe_name(device)}-{time.strftime('%Y-%m-%d_%H%M%S')}.tar.gz"
    tmp = d / (name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(d / name)
    prune(root, device, keep)
    return True, name


# ---------------------------------------------------------------- nova zarizeni

def is_random_mac(mac: str) -> bool:
    """Lokalne spravovana MAC (soukroma/nahodna adresa telefonu, virtualni rozhrani)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


class KnownDevices:
    """MAC, ktere add-on uz videl: {mac: {"first": ts, "last": ts}}. Prvni spusteni = vychozi stav bez upozorneni."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        self.baseline = True
        try:
            self.data = json.loads(path.read_text())
            self.baseline = False
        except (OSError, ValueError):
            pass

    def update(self, macs: set[str], now: float | None = None) -> list[str]:
        """Zapise videne MAC, vrati nove (pri prvnim spusteni zadne)."""
        now = now or time.time()
        new = []
        for m in macs:
            e = self.data.get(m)
            if e is None:
                self.data[m] = {"first": now, "last": now}
                if not self.baseline:
                    new.append(m)
            else:
                e["last"] = now
        self.baseline = False
        return sorted(new)

    def first_seen(self, mac: str) -> float:
        return (self.data.get(mac) or {}).get("first", 0)

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("Nelze ulozit %s: %s", self.path, e)
