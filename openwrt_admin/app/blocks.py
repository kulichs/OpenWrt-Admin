"""Blokovani zarizeni do internetu (firewall routeru + cron).

Pravidlo fw4: pojmenovana sekce firewall.owa_blk_<id> (stabilni – anonymni cfg… se pri mazani jinych pravidel
precisluji), src='*', dest=<WAN zona>, src_mac=<MAC…>, target REJECT.
Cas resi cron na routeru (mistni cas routeru), ne casove volby fw4 – u tech neni jiste UTC vs. mistni cas
a rozsahy pres pulnoc fw4 neresi:
  - trvale:   jen pravidlo (enabled=1)
  - rozvrh:   cron zapina (start) a vypina (stop) v zadane dny; stop pres pulnoc = dalsi den
  - docasne:  pravidlo hned zapnute, cron ho v danou minutu smaze i se svym radkem (prezije restart HA i routeru)
Radky cronu konci "# owadmin <sekce> <druh>", aby sly najit a smazat.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
import shlex

CRONTAB = "/etc/crontabs/root"
PREFIX = "owa_blk_"
SID_RE = re.compile(r"^owa_blk_[0-9a-f]{6,12}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]          # ISO 1..7
CRON_DOW = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
UCI = "/sbin/uci"
FWRELOAD = "/etc/init.d/firewall reload"


def new_sid() -> str:
    return PREFIX + secrets.token_hex(4)


def q(v) -> str:
    return shlex.quote(str(v))


def parse_now(text: str) -> dt.datetime | None:
    """`date '+%Y-%m-%d %H:%M'` z routeru -> naivni mistni cas routeru."""
    try:
        return dt.datetime.strptime((text or "").strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


# ---------------------------------------------------------------- cron radky

def _rule_cmd(sid: str, enabled: bool) -> str:
    return f"{UCI} set firewall.{sid}.enabled={1 if enabled else 0} && {UCI} commit firewall && {FWRELOAD}"


def _del_cmd(sid: str) -> str:
    return (f"{UCI} -q delete firewall.{sid}; {UCI} commit firewall && {FWRELOAD}; "
            f"sed -i '/# owadmin {sid} /d' {CRONTAB}")


def schedule_lines(sid: str, start: str, stop: str, days: list[str]) -> list[str]:
    """Rozvrh: zapnout v start, vypnout v stop (kdyz stop <= start, tak dalsi den)."""
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, stop.split(":"))
    days = [d for d in DAYS if d in days] or DAYS[:]
    on_dow = ",".join(str(CRON_DOW[d]) for d in days)
    overnight = (h2, m2) <= (h1, m1)
    off_days = [DAYS[(DAYS.index(d) + 1) % 7] for d in days] if overnight else days
    off_dow = ",".join(str(CRON_DOW[d]) for d in DAYS if d in off_days)
    return [f"{m1} {h1} * * {on_dow} {_rule_cmd(sid, True)} # owadmin {sid} on",
            f"{m2} {h2} * * {off_dow} {_rule_cmd(sid, False)} # owadmin {sid} off"]


def until_line(sid: str, until: dt.datetime) -> str:
    return f"{until.minute} {until.hour} {until.day} {until.month} * {_del_cmd(sid)} # owadmin {sid} until"


def in_window(now: dt.datetime, start: str, stop: str, days: list[str]) -> bool:
    """Je ted (cas routeru) uvnitr rozvrhu? Pres pulnoc se rano pocita k predchozimu dni."""
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, stop.split(":"))
    cur = (now.hour, now.minute)
    today = DAYS[now.isoweekday() - 1]
    yesterday = DAYS[(now.isoweekday() - 2) % 7]
    if (h2, m2) > (h1, m1):
        return today in days and (h1, m1) <= cur < (h2, m2)
    return (today in days and cur >= (h1, m1)) or (yesterday in days and cur < (h2, m2))


# ---------------------------------------------------------------- prikazy

def crontab_cmds(sid: str, lines: list[str]) -> list[str]:
    cmds = [f"touch {CRONTAB}", f"sed -i '/# owadmin {sid} /d' {CRONTAB}"]
    cmds += [f"echo {q(line)} >> {CRONTAB}" for line in lines]
    return cmds


def create_cmds(sid: str, label: str, macs: list[str], wan_zone: str, enabled: bool, lines: list[str]) -> list[str]:
    cmds = [f"uci set firewall.{sid}=rule", f"uci set firewall.{sid}.name={q('OWA: ' + label)}",
            f"uci set firewall.{sid}.src='*'", f"uci set firewall.{sid}.dest={q(wan_zone)}",
            f"uci set firewall.{sid}.target=REJECT", f"uci set firewall.{sid}.enabled={1 if enabled else 0}"]
    cmds += [f"uci add_list firewall.{sid}.src_mac={q(m.upper())}" for m in macs]
    if lines:
        cmds += crontab_cmds(sid, lines)
    return cmds


def delete_cmds(sid: str) -> list[str]:
    return [f"uci -q delete firewall.{sid} || true", f"{{ [ ! -f {CRONTAB} ] || sed -i '/# owadmin {sid} /d' {CRONTAB}; }}"]


def reload_cmds(with_cron: bool) -> list[str]:
    r = [FWRELOAD]
    if with_cron:
        r = ["/etc/init.d/cron enable", "/etc/init.d/cron restart", FWRELOAD]
    return r


# ---------------------------------------------------------------- cteni zpet

def parse_cron(lines: list[str]) -> dict[str, dict]:
    """Radky '# owadmin <sid> on|off|until' -> {sid: {"mode": "schedule"|"until", ...}}."""
    out: dict[str, dict] = {}
    for line in lines:
        m = re.match(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+.*# owadmin (\S+) (on|off|until)\s*$", line)
        if not m:
            continue
        mi, h, dom, mon, dow, sid, kind = m.groups()
        e = out.setdefault(sid, {})
        if kind == "until":
            e.update(mode="until", until=(int(mon), int(dom), int(h), int(mi)))
        else:
            e["mode"] = "schedule"
            e[kind] = f"{int(h):02d}:{int(mi):02d}"
            if kind == "on":
                rev = {v: k for k, v in CRON_DOW.items()}
                e["days"] = [rev[int(x)] for x in dow.split(",") if x.isdigit() and int(x) in rev]
    return out


def until_dt(now: dt.datetime, mon: int, dom: int, h: int, mi: int) -> dt.datetime:
    """Cron nema rok – vezme nejblizsi takovy termin od ted (nebo letos, kdyz uz probehl = proslo)."""
    for year in (now.year, now.year + 1):
        try:
            t = dt.datetime(year, mon, dom, h, mi)
        except ValueError:
            continue
        if t >= now - dt.timedelta(days=200):
            return t
    return dt.datetime(now.year, mon, dom, h, mi)


def collect(fw: dict, cron_lines: list[str], now: dt.datetime | None, names: dict[str, str]) -> list[dict]:
    """Blokace pro stranku: pravidla aplikace (owa_blk_*) a ostatni blokovaci pravidla (jen zap/vyp)."""
    cron = parse_cron(cron_lines)
    out = []
    for r in fw.get("rules", []):
        own = r["id"].startswith(PREFIX)
        if not own and r.get("kind") != "block":
            continue
        e = {"id": r["id"], "own": own, "name": r["name"][5:] if own and r["name"].startswith("OWA: ") else r["name"],
             "macs": r["src_mac"], "ips": r["src_ip"], "enabled": r["enabled"], "dest": r["dest"],
             "devices": [names.get(m.lower(), "") for m in r["src_mac"]], "mode": "permanent", "time": r.get("time") or {},
             "ports": r.get("dest_port", ""), "proto": r.get("proto", [])}
        c = cron.get(r["id"]) if own else None
        if c and c.get("mode") == "until" and now:
            t = until_dt(now, *c["until"])
            e.update(mode="until", until=t.strftime("%Y-%m-%d %H:%M"), left=int((t - now).total_seconds()),
                     expired=t <= now)
        elif c and c.get("mode") == "schedule":
            e.update(mode="schedule", start=c.get("on", "?"), stop=c.get("off", "?"), days=c.get("days", []))
        elif e["time"]:
            e["mode"] = "fwtime"
        out.append(e)
    return out


# ---------------------------------------------------------------- vstupy

def norm_time(s: str) -> str | None:
    m = TIME_RE.match((s or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def parse_until(s: str) -> dt.datetime | None:
    """datetime-local z prohlizece ('2026-11-27T07:00')."""
    try:
        return dt.datetime.strptime((s or "").strip()[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
