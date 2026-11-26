"""Sluzby na zarizenich (procd/init.d) a rozsireni adblock a banIP.

Jedno SSH spojeni = jeden prikaz SVC_CMD, vystup rozdeleny znackami @@<sekce>. Cteni je jen cteni (zadna zmena),
akce se skladaji jako seznam shell prikazu -> nahled -> Pouzit (viz main.py /services/*).
"""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
import time

MARK = "/tmp/owadmin-adb-resume"   # cas (epoch), kdy se ma adblock sam obnovit po pozastaveni z aplikace

# init skripty, ktere jen neco nastavi pri startu (zadny proces) – na strance Sluzby by jen prekazely
BOOT_ONLY = {"boot", "done", "sysfixtime", "umount", "urandom_seed", "sysctl", "led", "gpio_switch",
             "packet_steering", "bootcount", "cron.update"}
# bez techto se na zarizeni nedostaneme (SSH, sit) – jen restart, a to na pozadi; stop/disable ne
PROTECTED = {"dropbear", "network", "rpcd", "ubus", "log", "system", "procd"}
# jednorazove skripty: proces dobehne s kodem 0 a to je v poradku
ONESHOT = {"adblock", "banip"}
SVC_ACTIONS = ("start", "stop", "restart", "enable", "disable")
NAME_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$")

ADB_ALLOW = "/etc/adblock/adblock.allowlist"
ADB_BLOCK = "/etc/adblock/adblock.blocklist"
BAN_ALLOW = "/etc/banip/banip.allowlist"
BAN_BLOCK = "/etc/banip/banip.blocklist"

SVC_CMD = r"""echo @@services; ubus call service list 2>/dev/null
echo @@initd; ls /etc/init.d 2>/dev/null
echo @@rcd; ls /etc/rc.d 2>/dev/null
if [ -x /etc/init.d/adblock ]; then
 echo @@adblock; /etc/init.d/adblock status 2>&1 | head -40
 echo @@adb_meta
 echo "enabled $(uci -q get adblock.global.adb_enabled)"
 grep -qE 'extra_command[[:space:]]+"search"' /etc/init.d/adblock 2>/dev/null && echo "searchcmd search" || echo "searchcmd query"
 f=$(ls /tmp/dnsmasq.d/adb_list.overall /tmp/*/adb_list.overall /var/lib/unbound/adb_list.overall /var/lib/kresd/adb_list.overall 2>/dev/null | head -1)
 [ -n "$f" ] && echo "list $f $(wc -c < "$f") $(wc -l < "$f")"
 [ -f %(mark)s ] && echo "resume_at $(cat %(mark)s)"
 echo "now $(date +%%s)"
 echo @@adb_allow; grep -v '^[[:space:]]*#' %(aa)s 2>/dev/null | head -500
 echo @@adb_block; grep -v '^[[:space:]]*#' %(ab)s 2>/dev/null | head -500
fi
if [ -x /etc/init.d/banip ]; then
 echo @@banip; /etc/init.d/banip status 2>&1 | head -40
 echo @@ban_meta; echo "enabled $(uci -q get banip.global.ban_enabled)"
 echo @@ban_allow; grep -v '^[[:space:]]*#' %(ba)s 2>/dev/null | head -500
 echo @@ban_block; grep -v '^[[:space:]]*#' %(bb)s 2>/dev/null | head -500
fi
echo @@end
""" % {"mark": MARK, "aa": ADB_ALLOW, "ab": ADB_BLOCK, "ba": BAN_ALLOW, "bb": BAN_BLOCK}


# ---------------------------------------------------------------- parsery

def split_sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("@@") and re.fullmatch(r"@@[a-z_]+", line.strip()):
            cur = line.strip()[2:]
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def parse_status(text: str) -> dict[str, str]:
    """'  + key   : value' (adblock/banIP status) -> {key: value}."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\+\s*([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def parse_meta(text: str) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        k, _, v = line.strip().partition(" ")
        if k:
            out[k] = v.strip()
    return out


def parse_list(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and s not in out:
            out.append(s)
    return out


def enabled_from_rcd(initd_text: str, rcd_text: str) -> dict[str, bool]:
    """Spousteni po startu = odkaz S<NN><jmeno> v /etc/rc.d (totez, co dela `/etc/init.d/X enabled`, ale bez
    spousteni ~40 skriptu – to na routeru trva sekundy)."""
    started = set()
    for line in (rcd_text or "").split():
        m = re.match(r"^S\d+(.+)$", line.strip())
        if m:
            started.add(m.group(1))
    return {n: n in started for n in (initd_text or "").split() if NAME_RE.match(n)}


def parse_services(ubus_json: str, enabled_text: str | dict) -> list[dict]:
    """Seznam sluzeb: jmeno, bezi / dobehlo / zastaveno, spousteni po startu, chranena."""
    try:
        data = json.loads(ubus_json or "{}")
    except ValueError:
        data = {}
    enabled: dict[str, bool] = {}
    if isinstance(enabled_text, dict):
        enabled = dict(enabled_text)
    else:
        for line in (enabled_text or "").splitlines():
            n, _, v = line.strip().partition(" ")
            if n:
                enabled[n] = v.strip() == "1"
    names = (set(data) | set(enabled)) - BOOT_ONLY
    out = []
    for n in sorted(names):
        inst = (data.get(n) or {}).get("instances") or {}
        running = any(i.get("running") for i in inst.values())
        codes = [i.get("exit_code") for i in inst.values() if not i.get("running") and i.get("exit_code") is not None]
        if running:
            state = "running"
        elif n in data and not inst:
            state = "stopped"
        elif codes and all(c == 0 for c in codes) and n in ONESHOT:
            state = "done"
        elif codes and any(c != 0 for c in codes) and enabled.get(n) is not False:
            state = "failed"
        elif n not in data:
            state = "static"   # init skript bez procd sluzby (firewall, odhcpd…): nastavi se pri startu, bez procesu
        else:
            state = "stopped"
        out.append({"name": n, "state": state, "enabled": enabled.get(n), "in_procd": n in data,
                    "exit_codes": codes, "protected": n in PROTECTED,
                    "instances": len(inst), "pids": [i.get("pid") for i in inst.values() if i.get("pid")]})
    return out


def _int(v, default=None):
    try:
        return int(str(v).strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return default


def parse_adblock(sec: dict[str, str]) -> dict | None:
    if "adblock" not in sec:
        return None
    st = parse_status(sec.get("adblock", ""))
    meta = parse_meta(sec.get("adb_meta", ""))
    lst = meta.get("list", "").split()
    now = _int(meta.get("now"), int(time.time()))
    resume_at = _int(meta.get("resume_at"))
    list_bytes = _int(lst[1]) if len(lst) > 1 else None
    list_lines = _int(lst[2]) if len(lst) > 2 else None
    blocked = _int(st.get("blocked_domains"))
    enabled = meta.get("enabled", "") == "1" or st.get("adblock_status", "").lower() in ("enabled", "running")
    status = st.get("adblock_status", "").lower()
    has_mark = "resume_at" in meta          # pozastaveno z aplikace (0 = do rucniho obnoveni)
    empty = list_bytes == 0 or (blocked == 0 and not list_lines)
    if not enabled or status == "disabled":
        state = "disabled"
    elif status in ("paused", "suspend", "suspended"):
        state = "paused"
    elif empty:
        state = "paused" if has_mark else "empty"   # empty = pozastaveno odjinud, nebo se nestahly seznamy
    else:
        state = "ok"
    last = st.get("last_run", "")
    m = re.search(r"date\s*/\s*time:\s*([0-9-]+ [0-9:]+)", last)
    return {"state": state, "status": st.get("adblock_status", ""), "version": st.get("backend_ver", ""),
            "blocked": blocked if blocked else (list_lines or blocked), "feeds": st.get("active_feeds", ""),
            "dns": st.get("dns_backend", "").split(" (")[0], "last_run": m.group(1) if m else last,
            "list_file": lst[0] if lst else "", "list_bytes": list_bytes, "list_lines": list_lines,
            "resume_at": resume_at, "resume_in": (resume_at - now) if resume_at else None, "marked": has_mark,
            "searchcmd": meta.get("searchcmd") or "search",
            "allow": parse_list(sec.get("adb_allow", "")), "block": parse_list(sec.get("adb_block", "")),
            "raw": st}


def parse_banip(sec: dict[str, str]) -> dict | None:
    if "banip" not in sec:
        return None
    st = parse_status(sec.get("banip", ""))
    meta = parse_meta(sec.get("ban_meta", ""))
    enabled = meta.get("enabled", "") == "1"
    status = st.get("status", "").lower()
    if not enabled:
        state = "disabled"
    elif status.startswith(("active", "running")):
        state = "ok"
    else:
        state = "error"   # zapnuty, ale nebezi (nftables, chyba pri startu) – viz logread -e banIP
    return {"state": state, "enabled": enabled, "status": st.get("status", ""),
            "version": st.get("backend_ver") or st.get("frontend_ver", ""),
            "elements": st.get("element_count", ""), "feeds": st.get("active_feeds", ""),
            "last_run": st.get("last_run", ""), "allow": parse_list(sec.get("ban_allow", "")),
            "block": parse_list(sec.get("ban_block", "")), "raw": st}


def parse_all(text: str) -> dict:
    sec = split_sections(text)
    en = enabled_from_rcd(sec["initd"], sec.get("rcd", "")) if "initd" in sec else sec.get("enabled", "")
    return {"ts": time.time(), "services": parse_services(sec.get("services", ""), en),
            "adblock": parse_adblock(sec), "banip": parse_banip(sec), "error": "" if "end" in sec else "incomplete"}


# ---------------------------------------------------------------- vstupy

def norm_domain(d: str) -> str | None:
    d = (d or "").strip().lower().rstrip(".")
    if d.startswith(("http://", "https://")):
        d = d.split("://", 1)[1].split("/", 1)[0]
    d = d.split(":")[0] if d.count(":") == 1 else d
    return d if DOMAIN_RE.match(d) else None


def norm_ip(s: str) -> str | None:
    """IPv4/IPv6 adresa nebo sit (CIDR). Vraci normalizovany zapis, jinak None."""
    s = (s or "").strip()
    try:
        if "/" in s:
            return str(ipaddress.ip_network(s, strict=False))
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def valid_service(name: str, services: list[dict]) -> dict | None:
    if not NAME_RE.match(name or ""):
        return None
    return next((s for s in services if s["name"] == name), None)


# ---------------------------------------------------------------- prikazy akci

def svc_cmds(svc: dict, action: str) -> list[str] | None:
    if action not in SVC_ACTIONS:
        return None
    n = shlex.quote(svc["name"])
    if svc.get("protected"):
        if action != "restart":
            return None
        # bez SSH/site by se prikaz nedokoncil – restart na pozadi, az se odpojime
        return [f"{{ ( sleep 2; /etc/init.d/{n} restart ) >/dev/null 2>&1 </dev/null & }}"]
    return [f"/etc/init.d/{n} {action}"]


def adb_suspend_cmds(minutes: int, now: int | None = None) -> list[str]:
    """Pozastavit adblock; s minutes > 0 se sam obnovi (casovac na zarizeni, prezije restart HA)."""
    cmds = ["/etc/init.d/adblock suspend"]
    if minutes <= 0:
        cmds.append(f"echo 0 > {MARK}")   # 0 = pozastaveno do rucniho obnoveni
        return cmds
    at = int(now or time.time()) + minutes * 60
    cmds.append(f"echo {at} > {MARK}")
    check = (f"[ \"$(cat {MARK} 2>/dev/null)\" = \"{at}\" ] && {{ rm -f {MARK}; "
             f"logger -t openwrt-admin -p user.notice -- 'adblock resumed after {minutes} min'; /etc/init.d/adblock resume; }}")
    cmds.append(f"{{ ( sleep {minutes * 60}; {check} ) >/dev/null 2>&1 </dev/null & }}")
    return cmds


def adb_resume_cmds() -> list[str]:
    return [f"rm -f {MARK}", "/etc/init.d/adblock resume"]


def adb_reload_cmds() -> list[str]:
    return [f"rm -f {MARK}", "/etc/init.d/adblock reload"]


def _add_line(path: str, value: str) -> str:
    v, p = shlex.quote(value), shlex.quote(path)
    return f"{{ grep -qxF {v} {p} 2>/dev/null || echo {v} >> {p}; }}"


def _del_line(path: str, value: str) -> str:
    v, p = shlex.quote(value), shlex.quote(path)
    return (f"{{ [ ! -f {p} ] || {{ grep -vxF {v} {p} > /tmp/owadmin-list.tmp; cat /tmp/owadmin-list.tmp > {p}; "
            f"rm -f /tmp/owadmin-list.tmp; }}; }}")


def list_cmds(kind: str, target: str, op: str, value: str, reload: bool = True) -> list[str] | None:
    """kind adblock/banip, target allow/block, op add/del. Pridani na jeden seznam ho smaze z druheho."""
    files = {("adblock", "allow"): ADB_ALLOW, ("adblock", "block"): ADB_BLOCK,
             ("banip", "allow"): BAN_ALLOW, ("banip", "block"): BAN_BLOCK}
    if (kind, target) not in files or op not in ("add", "del"):
        return None
    path = files[(kind, target)]
    other = files[(kind, "block" if target == "allow" else "allow")]
    cmds = [_add_line(path, value), _del_line(other, value)] if op == "add" else [_del_line(path, value)]
    if reload:
        cmds.append(f"/etc/init.d/{kind} reload")
    return cmds


def search_cmd(kind: str, value: str, searchcmd: str = "search") -> str:
    if kind == "adblock":
        sub = "query" if searchcmd == "query" else "search"
        return f"/etc/init.d/adblock {sub} {shlex.quote(value)} 2>&1 | head -60"
    return f"/etc/init.d/banip search {shlex.quote(value)} 2>&1 | head -60"


# ---------------------------------------------------------------- demo

def demo_text(name: str, router: bool, state: dict | None = None) -> str:
    """Vystup SVC_CMD pro demo (stejny format jako ze zarizeni -> testuje i parsery)."""
    state = state or {}
    now = int(time.time())
    svc = {"dnsmasq": {"instances": {"cfg01411c": {"running": True, "pid": 2400}}},
           "dropbear": {"instances": {"instance1": {"running": True, "pid": 1203}}},
           "network": {"instances": {"instance1": {"running": True, "pid": 1010}}},
           "log": {"instances": {"instance1": {"running": True, "pid": 900}}},
           "collectd": {"instances": {"instance1": {"running": True, "pid": 3120}}},
           "cron": {}, "rpcd": {"instances": {"instance1": {"running": True, "pid": 1100}}}}
    en = ["boot 1", "cron 0", "dnsmasq 1", "dropbear 1", "network 1", "log 1", "collectd 1", "rpcd 1", "sysntpd 1"]
    if not router:
        svc.pop("dnsmasq")
        svc["wpad"] = {"instances": {"hostapd": {"running": True, "pid": 1500}, "wpa_supplicant": {"running": True, "pid": 1501}}}
        en += ["wpad 1"]
        return "@@services\n" + json.dumps(svc) + _demo_rc(en) + "\n@@end\n"
    svc["adblock"] = {"instances": {"adblock": {"running": False, "exit_code": 0}}}
    svc["banip"] = {"instances": {"banip-service": {"running": False, "exit_code": 1}}}
    svc["nlbwmon"] = {"instances": {"instance1": {"running": True, "pid": 2001}}}
    svc["sqm"] = {}
    en += ["adblock 1", "banip 0", "nlbwmon 1", "sqm 0", "firewall 1", "odhcpd 1"]
    paused = state.get("adb_paused_until", 0) > now or state.get("adb_paused_until") == -1
    allow = state.get("adb_allow", ["ads.example-shop.cz", "tracking.bank.example"])
    block = state.get("adb_block", ["telemetry.tv-vendor.example"])
    lines = 0 if paused else 98213
    adb = f"""::: adblock runtime information
  + adblock_status  : enabled
  + frontend_ver    : 4.5.9-r2
  + backend_ver     : 4.5.9-r5
  + blocked_domains : {lines}
  + active_feeds    : adguard, disconnect, yoyo
  + dns_backend     : dnsmasq (2.93-r2), /tmp/dnsmasq.d, 2.17 MB
  + last_run        : mode: start, date / time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now - 7200))}, duration: 0m 18s, memory: 891.50 MB available"""
    meta = [f"enabled 1", "searchcmd search", f"list /tmp/dnsmasq.d/adb_list.overall {lines * 24} {lines}", f"now {now}"]
    if state.get("adb_paused_until", 0) > now:
        meta.append(f"resume_at {int(state['adb_paused_until'])}")
    elif state.get("adb_paused_until") == -1:
        meta.append("resume_at 0")
    ban_allow = state.get("ban_allow", ["203.0.113.10"])
    ban_block = state.get("ban_block", ["198.51.100.0/24"])
    return ("@@services\n" + json.dumps(svc) + _demo_rc(en) + "\n@@adblock\n" + adb +
            "\n@@adb_meta\n" + "\n".join(meta) + "\n@@adb_allow\n" + "\n".join(allow) + "\n@@adb_block\n" + "\n".join(block) +
            "\n@@banip\n::: no banIP runtime information available\n@@ban_meta\nenabled 0\n@@ban_allow\n" +
            "\n".join(ban_allow) + "\n@@ban_block\n" + "\n".join(ban_block) + "\n@@end\n")


def _demo_rc(en: list[str]) -> str:
    names = [x.split()[0] for x in en]
    on = [f"S{10 + i:02d}{x.split()[0]}" for i, x in enumerate(en) if x.endswith(" 1")]
    return "\n@@initd\n" + "\n".join(names) + "\n@@rcd\n" + "\n".join(on)


def demo_search(kind: str, value: str, adb: dict | None) -> str:
    if kind == "adblock":
        hit = value in ("doubleclick.net", "googleadservices.com") or value.endswith(".doubleclick.net")
        allowed = adb and value in adb.get("allow", [])
        return (f":::\n::: domain '{value}' in active blocklist\n:::\n  " + ("+ " + value if hit and not allowed else "- no match") +
                f"\n\n:::\n::: domain '{value}' in backups and in local block-/allowlist\n:::\n  " +
                ("+ adb_list.adguard.gz           " + value + "\n  + adb_list.yoyo.gz              " + value if hit else
                 ("+ adblock.allowlist             " + value if allowed else "- no match")))
    return (f":::\n::: banIP Search\n:::\n    Looking for IP '{value}' on {time.strftime('%Y-%m-%d %H:%M:%S')}\n    ---\n" +
            ("    IP found in Set 'blocklistv4'" if value.startswith("198.51.100.") else "    IP not found"))
