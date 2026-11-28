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


HASH_MARK = "@@cfghash"
HEX32 = re.compile(r"^[0-9a-f]{32}$")


def hash_cmd(pkgs: list[str]) -> str:
    """Pripojit za cteni konfigurace: otisk kazdeho balicku (uci show | md5sum) -> radky '<balik> <md5>'."""
    assert all(PKG_RE.match(p) for p in pkgs)
    return f"echo {HASH_MARK}; for p in {' '.join(pkgs)}; do echo \"$p $(uci -q show $p | md5sum | cut -c1-32)\"; done"


def split_hash(out: str) -> tuple[str, dict[str, str]]:
    """Vystup cteni -> (puvodni vystup, {balik: md5})."""
    head, mark, tail = (out or "").rpartition(HASH_MARK + "\n")
    if not mark:
        return out, {}
    hashes = {}
    for line in tail.splitlines():
        p, _, h = line.strip().partition(" ")
        if PKG_RE.match(p) and HEX32.match(h):
            hashes[p] = h
    return head.rstrip("\n"), hashes


def guard(base: dict[str, str] | None) -> str:
    """Na zacatek zmeny: kdyz se konfigurace od nahledu zmenila (LuCI, jina zalozka), skoncit 'STALE' a nic nemenit."""
    if not base:
        return ""
    assert all(PKG_RE.match(p) and HEX32.match(h) for p, h in base.items())
    checks = " && ".join(f'[ "$(uci -q show {p} | md5sum | cut -c1-32)" = "{h}" ]' for p, h in sorted(base.items()))
    return f"{{ {checks}; }} || {{ echo STALE; exit 1; }}; "


def new_token() -> str:
    return secrets.token_hex(6)


def arm_script(token: str, pkgs: list[str], cmds: list[str], reload: list[str], timeout: int = 90,
               confdir: str = "/etc/config", base: str = BASE, check: str | None = None,
               files: list[str] | None = None, cfg_base: dict[str, str] | None = None) -> str:
    """files = dalsi soubory mimo uci (napr. /etc/crontabs/root) – zalohuji se a vraceji spolu s konfiguraci."""
    """Skript pro zarizeni: zaloha, odpocet s navratem, zmena. Konci 'ARMED <token>' nebo chybou (nic nezmeneno)."""
    assert all(PKG_RE.match(p) for p in pkgs) and re.fullmatch(r"[0-9a-f]{6,32}", token)
    d = f"{base}/{token}"
    rel = " && ".join(reload) or "true"
    files = files or []
    assert all(re.fullmatch(r"/[A-Za-z0-9_./-]{1,120}", f) and ".." not in f for f in files)
    backup = " && ".join([f"cp {confdir}/{p} {d}/{p}" for p in pkgs] +
                         [f"{{ if [ -f {f} ]; then cp {f} {d}/file{i}; else touch {d}/file{i}.none; fi; }}"
                          for i, f in enumerate(files)])
    restore = " && ".join([f"cp {d}/{p} {confdir}/{p}" for p in pkgs] +
                          [f"{{ if [ -f {d}/file{i}.none ]; then rm -f {f}; else cp {d}/file{i} {f}; fi; }}"
                           for i, f in enumerate(files)])
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
    return (f"{guard(cfg_base)}mkdir -p {d} && {backup} || {{ rm -rf {d}; echo FAILED; exit 1; }}; {change}; "
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
