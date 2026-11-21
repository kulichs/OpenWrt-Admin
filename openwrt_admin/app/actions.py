"""Akce na AP: restart, aktualizace balicku (apk/opkg), zmena SSID pres uci."""
from __future__ import annotations

import asyncio
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


async def run(cfg: APConfig, key: asyncssh.SSHKey, cmd: str, timeout: float = 60.0) -> Result:
    try:
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[key],
                                    known_hosts=None, connect_timeout=10) as conn:
            res = await asyncio.wait_for(conn.run(cmd, check=False, stderr=asyncssh.STDOUT), timeout)
            out = res.stdout if isinstance(res.stdout, str) else (res.stdout or b"").decode(errors="replace")
            return Result(res.exit_status == 0, out.strip() or "(bez výstupu)")
    except asyncio.TimeoutError:
        return Result(False, f"Vypršel časový limit {int(timeout)} s. Příkaz může na AP dál běžet.")
    except (OSError, asyncssh.Error) as e:
        return Result(False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- restart

async def reboot(cfg: APConfig, key) -> Result:
    # odpojit se driv, nez AP spadne
    r = await run(cfg, key, "(sleep 2; reboot) >/dev/null 2>&1 </dev/null &", timeout=15)
    if r.ok:
        r.output = "Restart odeslán. AP bude asi 1–2 minuty nedostupný."
    return r


# ---------------------------------------------------------------- balicky

def update_cmd(pkg: str) -> str:
    if pkg == "opkg":
        return "opkg update >/tmp/owadmin-update.log 2>&1 || { tail -n 20 /tmp/owadmin-update.log; exit 1; }; " \
               "echo '== Dostupné aktualizace =='; opkg list-upgradable"
    return "apk update 2>&1 || exit 1; echo; echo '== Dostupné aktualizace =='; apk list --upgradable 2>&1"


def upgrade_cmd(pkg: str) -> str:
    if pkg == "opkg":
        return ("opkg update >/dev/null 2>&1; P=$(opkg list-upgradable | cut -d' ' -f1); "
                "[ -z \"$P\" ] && { echo 'Vše je aktuální.'; exit 0; }; opkg upgrade $P 2>&1")
    return "apk update >/dev/null 2>&1; apk upgrade 2>&1"


async def pkg_update(cfg: APConfig, key, pkg: str) -> Result:
    return await run(cfg, key, update_cmd(pkg), timeout=120)


async def pkg_upgrade(cfg: APConfig, key, pkg: str) -> Result:
    return await run(cfg, key, upgrade_cmd(pkg), timeout=900)


# ---------------------------------------------------------------- SSID

@dataclass
class SsidChange:
    section: str
    ssid: str | None = None       # None = beze zmeny
    key: str | None = None        # None = beze zmeny
    hidden: bool | None = None
    disabled: bool | None = None


def validate(ch: SsidChange) -> str | None:
    if not SECTION_RE.match(ch.section):
        return f"Neplatná uci sekce: {ch.section!r}"
    if ch.ssid is not None and not (1 <= len(ch.ssid.encode()) <= 32):
        return "SSID musí mít 1–32 bajtů."
    if ch.key is not None and not (8 <= len(ch.key) <= 63 and ch.key.isascii() and ch.key.isprintable()):
        return "Heslo musí mít 8–63 tisknutelných ASCII znaků."
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


async def apply_ssid(cfg: APConfig, key, changes: list[SsidChange]) -> Result:
    cmds = [c for ch in changes for c in uci_commands(ch)]
    if not cmds:
        return Result(True, "Beze změny.")
    script = " && ".join(cmds) + " && uci commit wireless && wifi reload && echo OK"
    log.info("%s: %s", cfg.name, " ; ".join(mask(c) for c in cmds))
    r = await run(cfg, key, f"{script} || {{ uci revert wireless; echo 'Chyba. Necommitnuté změny vráceny (uci revert), zkontroluj stav AP.'; exit 1; }}",
                  timeout=60)
    return r
