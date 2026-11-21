"""Akce na AP: restart, aktualizace balicku (apk/opkg), zmena SSID pres uci."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from dataclasses import dataclass

import asyncssh

from .collector import APConfig

log = logging.getLogger("openwrt_admin.actions")

SECTION_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass
class Result:
    ok: bool
    output: str


async def run(cfg: APConfig, key: asyncssh.SSHKey, cmd: str, timeout: float = 60.0, t=None) -> Result:
    try:
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[key],
                                    known_hosts=None, connect_timeout=10) as conn:
            res = await asyncio.wait_for(conn.run(cmd, check=False, stderr=asyncssh.STDOUT), timeout)
            out = res.stdout if isinstance(res.stdout, str) else (res.stdout or b"").decode(errors="replace")
            return Result(res.exit_status == 0, out.strip() or (t("msg.no_output") if t else "(no output)"))
    except asyncio.TimeoutError:
        return Result(False, t("msg.timeout", s=int(timeout)) if t else f"Timeout after {int(timeout)} s.")
    except (OSError, asyncssh.Error) as e:
        return Result(False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- restart

async def reboot(cfg: APConfig, key, t) -> Result:
    # odpojit se driv, nez AP spadne
    r = await run(cfg, key, "(sleep 2; reboot) >/dev/null 2>&1 </dev/null &", timeout=15, t=t)
    if r.ok:
        r.output = t("msg.reboot_sent")
    return r


# ---------------------------------------------------------------- balicky

def update_cmd(pkg: str, header: str = "== Upgradable ==") -> str:
    if pkg == "opkg":
        return "opkg update >/tmp/owadmin-update.log 2>&1 || { tail -n 20 /tmp/owadmin-update.log; exit 1; }; " \
               f"echo {shlex.quote(header)}; opkg list-upgradable"
    return f"apk update 2>&1 || exit 1; echo; echo {shlex.quote(header)}; apk list --upgradable 2>&1"


def upgrade_cmd(pkg: str, uptodate: str = "Everything is up to date.") -> str:
    if pkg == "opkg":
        return ("opkg update >/dev/null 2>&1; P=$(opkg list-upgradable | cut -d' ' -f1); "
                f"[ -z \"$P\" ] && {{ echo {shlex.quote(uptodate)}; exit 0; }}; opkg upgrade $P 2>&1")
    return "apk update >/dev/null 2>&1; apk upgrade 2>&1"


async def pkg_update(cfg: APConfig, key, pkg: str, t) -> Result:
    return await run(cfg, key, update_cmd(pkg, t("msg.upgradable_header")), timeout=120, t=t)


async def pkg_upgrade(cfg: APConfig, key, pkg: str, t) -> Result:
    return await run(cfg, key, upgrade_cmd(pkg, t("msg.up_to_date")), timeout=900, t=t)


# ---------------------------------------------------------------- SSID

@dataclass
class SsidChange:
    section: str
    ssid: str | None = None       # None = beze zmeny
    key: str | None = None        # None = beze zmeny
    hidden: bool | None = None
    disabled: bool | None = None


def validate(ch: SsidChange, t) -> str | None:
    if not SECTION_RE.match(ch.section):
        return t("msg.bad_section", section=ch.section)
    if ch.ssid is not None and not (1 <= len(ch.ssid.encode()) <= 32):
        return t("msg.bad_ssid")
    if ch.key is not None and not (8 <= len(ch.key) <= 63 and ch.key.isascii() and ch.key.isprintable()):
        return t("msg.bad_key")
    return None


def uci_commands(ch: SsidChange) -> list[str]:
    s = f"wireless.{ch.section}"
    out = []
    if ch.ssid is not None:
        out.append(f"uci set {s}.ssid={shlex.quote(ch.ssid)}")
    if ch.key is not None:
        out.append(f"uci set {s}.key={shlex.quote(ch.key)}")
    if ch.hidden is not None:
        out.append(f"uci set {s}.hidden={'1' if ch.hidden else '0'}")
    if ch.disabled is not None:
        out.append(f"uci set {s}.disabled={'1' if ch.disabled else '0'}")
    return out


def mask(cmd: str) -> str:
    """Heslo v nahledu a logu nezobrazovat."""
    return re.sub(r"(\.key=).*$", r"\1********", cmd)


async def apply_ssid(cfg: APConfig, key, changes: list[SsidChange], t) -> Result:
    cmds = [c for ch in changes for c in uci_commands(ch)]
    if not cmds:
        return Result(True, t("msg.unchanged"))
    script = " && ".join(cmds) + " && uci commit wireless && wifi reload && echo OK"
    log.info("%s: %s", cfg.name, " ; ".join(mask(c) for c in cmds))
    r = await run(cfg, key, f"{script} || {{ uci revert wireless; echo {shlex.quote(t('msg.reverted'))}; exit 1; }}",
                  timeout=60, t=t)
    return r


# ---------------------------------------------------------------- odpojeni klienta

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
IFNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
BAN_CHOICES = (0, 600, 3600, 86400)  # s: jen odpojit, 10 min, 1 h, 24 h


def kick_cmd(ifname: str, mac: str, ban: int) -> str:
    """hostapd del_client: deauth; ban_time (ms) drzi hostapd v pameti – do restartu Wi-Fi/AP."""
    if not IFNAME_RE.match(ifname) or not MAC_RE.match(mac) or ban not in BAN_CHOICES:
        raise ValueError("bad kick args")
    args = {"addr": mac, "reason": 5, "deauth": True, "ban_time": ban * 1000}
    return f"ubus call hostapd.{ifname} del_client {shlex.quote(json.dumps(args))}"


async def kick(cfg: APConfig, key, targets: list[str], mac: str, ban: int, t) -> Result:
    """targets = ifname na tomto AP (vic, kdyz se blokuje na vsech rozhranich se stejnym SSID)."""
    cmd = " ; ".join(f"{kick_cmd(i, mac, ban)} && echo {shlex.quote(cfg.name + ' ' + i + ': OK')}" for i in targets)
    return await run(cfg, key, cmd, timeout=20, t=t)
