"""Ulozit s pojistkou (jako LuCI „apply with rollback“).

1. Zarizeni si zazalohuje dotcene konfigurace (/etc/config/<pkg>) do /tmp/owadmin-rb/<token>/.
2. Spusti na pozadi odpocet: kdyz do `timeout` s nevznikne soubor `ok`, vrati zalohy a znovu nacte sluzby.
3. Provede zmeny (uci … && uci commit && reload) – na pozadi, aby je neprerusilo odpojeni SSH.
4. Aplikace se po chvili znovu pripoji novym SSH spojenim a potvrdi (`touch …/ok`). Kdyz se pripojit nejde,
   zarizeni se po odpoctu samo vrati – zmena, ktera odrizne sit, nevydrzi.
"""
from __future__ import annotations

import re
import secrets
import shlex

BASE = "/tmp/owadmin-rb"
PKG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def new_token() -> str:
    return secrets.token_hex(6)


def arm_script(token: str, pkgs: list[str], cmds: list[str], reload: list[str], timeout: int = 90,
               confdir: str = "/etc/config", base: str = BASE, check: str | None = None) -> str:
    """Skript pro zarizeni: zaloha, odpocet s navratem, zmena. Konci 'ARMED <token>' nebo chybou (nic nezmeneno)."""
    assert all(PKG_RE.match(p) for p in pkgs) and re.fullmatch(r"[0-9a-f]{6,32}", token)
    d = f"{base}/{token}"
    rel = " && ".join(reload) or "true"
    backup = " && ".join(f"cp {confdir}/{p} {d}/{p}" for p in pkgs)
    restore = " && ".join(f"cp {d}/{p} {confdir}/{p}" for p in pkgs)
    commits = " && ".join(f"uci commit {p}" for p in pkgs)
    reverts = "; ".join(f"uci revert {p}" for p in pkgs)
    timer = (f"( sleep {int(timeout)}; if [ ! -f {d}/ok ]; then {restore}; {rel}; "
             f"logger -t openwrt-admin -p user.warning -- {shlex.quote(f'rollback {token}: not confirmed, restored ' + ' '.join(pkgs))}; "
             f"echo rolledback > {d}/state; else echo confirmed > {d}/state; fi ) >/dev/null 2>&1 </dev/null &")
    chk = f" && {check}" if check else ""
    # pri chybe (i v kontrole po commitu) vratit zalohy – nic se nezmeni
    change = (f"{{ {' && '.join(cmds)} && {commits}{chk}; }} || {{ {reverts}; {restore}; rm -rf {d}; echo FAILED; "
              f"exit 1; }}")
    apply_bg = f"( sleep 1; {rel} ) >{d}/reload.log 2>&1 </dev/null &"
    return (f"mkdir -p {d} && {backup} || {{ rm -rf {d}; echo FAILED; exit 1; }}; {change}; "
            f"echo armed > {d}/state; {timer} {apply_bg} echo ARMED {token}")


def confirm_cmd(token: str, base: str = BASE) -> str:
    assert re.fullmatch(r"[0-9a-f]{6,32}", token)
    d = f"{base}/{token}"
    return f"[ -d {d} ] && touch {d}/ok && echo CONFIRMED || echo GONE"


def state_cmd(token: str, base: str = BASE) -> str:
    assert re.fullmatch(r"[0-9a-f]{6,32}", token)
    return f"cat {base}/{token}/state 2>/dev/null || echo gone"


def cleanup_cmd(base: str = BASE) -> str:
    """Stare tokeny (potvrzene/vracene) smazat – volat obcas, neskodne."""
    return f"find {base} -maxdepth 1 -mindepth 1 -type d -mmin +30 -exec rm -rf {{}} + 2>/dev/null; true"
