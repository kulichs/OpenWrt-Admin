"""Statistiky site: rychlosti rozhrani routeru, teplota, ping, top klienti v internetu, provoz na portech switche
a aktualni rychlost Wi-Fi klientu.

Zdroje (na zarizenich se nic neinstaluje):
- router, jeden prikaz pres trvale SSH spojeni kazdych `stats_interval` s:
  /proc/net/dev (citace rozhrani), /sys/class/thermal (teplota), /proc/loadavg, ping na cile z konfigurace
  (a na AP, switche a branu poskytovatele), provoz klientu z nlbwmon (`nlbw -c json`), kdyz je nainstalovany,
  jinak z conntracku (/proc/net/nf_conntrack, potrebuje net.netfilter.nf_conntrack_acct=1 – vychozi v OpenWrt),
- switche: citace portu pres SNMP (IF-MIB ifHCInOctets/ifHCOutOctets, zalozne 32bitove ifIn/OutOctets),
- Wi-Fi klienti: rozdil rx/tx bajtu z hostapd mezi dvema nactenimi AP (poll_interval).

Historie se drzi jen v pameti (posledni hodina). Dlouhodobou historii ma Home Assistant, kdyz se zapnou senzory
(`ha_sensors`). Parsery jsou ciste funkce (parse_*), testovatelne na skutecnych vystupech.
"""
from __future__ import annotations

import json
import math
import random
import re
import shlex
import time
import zlib
from collections import deque
from dataclasses import dataclass, field

HISTORY = 3600            # s historie v pameti
WINDOW_NLBW = 90          # s – nlbwmon aktualizuje data po refresh_interval (vychozi 30 s), prumer pres delsi okno
WINDOW_CT = 30            # s – conntrack citace jsou zive
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,252}$")
PING_SLOW_MS = 100.0



# ---------------------------------------------------------------- casove rady

class Series:
    """Casova rada (ts, hodnota) s pevnou delkou."""

    def __init__(self, maxlen: int):
        self.d: deque = deque(maxlen=maxlen)

    def add(self, ts: float, v):
        self.d.append((ts, v))

    @property
    def last(self):
        return self.d[-1][1] if self.d else None

    def values(self) -> list:
        return [v for _, v in self.d]

    def peak(self) -> float:
        return max((v for _, v in self.d if v is not None), default=0.0)

    def avg(self) -> float | None:
        vals = [v for _, v in self.d if v is not None]
        return sum(vals) / len(vals) if vals else None


def counter_delta(prev: int, cur: int, bits: int = 64) -> int | None:
    """Rozdil citace; pretoceni 32bitoveho citace se dopocita, reset (restart zarizeni) -> None."""
    d = cur - prev
    if d >= 0:
        return d
    if bits == 32 and prev > 2**31:
        return d + 2**32
    return None


# ---------------------------------------------------------------- parsery (ciste funkce)

def parse_sections(text: str) -> dict[str, str]:
    """Vystup prikazu routeru rozdeleny podle radku '@@nazev' -> {nazev: text}."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            out.setdefault(cur, [])
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def parse_net_dev(text: str) -> dict[str, tuple[int, int, int, int]]:
    """/proc/net/dev -> {zarizeni: (rx_bytes, rx_packets, tx_bytes, tx_packets)}."""
    out = {}
    for line in (text or "").splitlines():
        if ":" not in line or "|" in line:
            continue
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 10 and all(x.isdigit() for x in (f[0], f[1], f[8], f[9])):
            out[name.strip()] = (int(f[0]), int(f[1]), int(f[8]), int(f[9]))
    return out


def parse_thermal(text: str) -> list[tuple[str, float]]:
    """'typ teplota' po radcich (thermal_zone*/type, temp v m°C) -> [(typ, °C)]."""
    out = []
    for line in (text or "").splitlines():
        p = line.split()
        if not p:
            continue
        try:
            v = float(p[-1])
        except ValueError:
            continue
        if abs(v) >= 1000:
            v /= 1000
        if -40 < v < 150:
            out.append((" ".join(p[:-1]) or f"zone{len(out)}", round(v, 1)))
    return out


def cpu_temp(zones: list[tuple[str, float]]) -> float | None:
    """Teplota CPU: zona s 'cpu'/'soc' v nazvu, jinak nejteplejsi."""
    if not zones:
        return None
    for name, v in zones:
        if any(k in name.lower() for k in ("cpu", "soc")):
            return v
    return max(v for _, v in zones)


def parse_loadavg(text: str) -> float | None:
    try:
        return float((text or "").split()[0])
    except (IndexError, ValueError):
        return None


PING_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms")
PING_AVG_RE = re.compile(r"min/avg/max\S*\s*=\s*[\d.]+/([\d.]+)/")


def parse_ping(text: str) -> float | None:
    """Vystup `ping -c N` (BusyBox i iputils) -> prumerna odezva v ms, None = bez odpovedi."""
    m = PING_AVG_RE.search(text or "")
    if m:
        return float(m.group(1))
    m = PING_TIME_RE.search(text or "")
    return float(m.group(1)) if m else None


def parse_nlbw(text: str) -> dict[str, dict]:
    """`nlbw -c json -g mac,ip` -> {mac: {mac, ips, rx, tx}} (rx = download klienta, tx = upload; soucet pres IP)."""
    try:
        d = json.loads(text or "")
    except ValueError:
        return {}
    cols = d.get("columns") or []
    out: dict[str, dict] = {}
    for row in d.get("data") or []:
        if not isinstance(row, list):
            continue
        r = dict(zip(cols, row))
        mac = str(r.get("mac") or "").lower()
        ip = str(r.get("ip") or "")
        key = mac if mac and mac != "00:00:00:00:00:00" else ip
        if not key:
            continue
        e = out.setdefault(key, {"mac": mac if key == mac else "", "ips": [], "rx": 0, "tx": 0})
        if ip and ip not in e["ips"]:
            e["ips"].append(ip)
        e["rx"] += int(r.get("rx_bytes") or 0)
        e["tx"] += int(r.get("tx_bytes") or 0)
    for e in out.values():  # IPv4 napred
        e["ips"].sort(key=lambda x: (":" in x, x))
    return out


CT_SRC = re.compile(r"\bsrc=(\S+)")
CT_DST = re.compile(r"\bdst=(\S+)")
CT_SPORT = re.compile(r"\bsport=(\d+)")
CT_DPORT = re.compile(r"\bdport=(\d+)")
CT_BYTES = re.compile(r"\bbytes=(\d+)")


def parse_conntrack(text: str, is_local) -> dict[str, tuple[str, int, int]]:
    """/proc/net/nf_conntrack -> {spojeni: (lokalni IP, upload bajtu, download bajtu)} jen pro provoz LAN <-> internet.
    Odchozi (src v LAN, protistrana mimo LAN): upload = puvodni smer. Prichozi pres DNAT (odpoved z LAN):
    upload = smer odpovedi. Provoz mezi LAN/VLAN a provoz routeru samotneho se nepocita."""
    out = {}
    for line in (text or "").splitlines():
        b = CT_BYTES.findall(line)
        s = CT_SRC.findall(line)
        dd = CT_DST.findall(line)
        if len(b) < 2 or len(s) < 2 or len(dd) < 1:
            continue
        osrc, odst, rsrc = s[0], dd[0], s[1]
        sp = CT_SPORT.findall(line)
        dp = CT_DPORT.findall(line)
        parts = line.split()
        proto = parts[2] if len(parts) > 2 else "?"
        key = f"{proto} {osrc} {odst} {sp[0] if sp else ''} {dp[0] if dp else ''}"
        if is_local(osrc) and not is_local(odst) and not is_local(rsrc):
            out[key] = (osrc, int(b[0]), int(b[1]))
        elif not is_local(osrc) and is_local(rsrc):
            out[key] = (rsrc, int(b[1]), int(b[0]))
    return out


# ---------------------------------------------------------------- prikaz pro router

def valid_host(h: str) -> bool:
    return bool(HOST_RE.match(h or ""))


def router_cmd(pings: list[str], traffic: str = "auto") -> str:
    """Jeden prikaz (BusyBox ash) pro router. pings: adresy k pingnuti (v poradi, overene HOST_RE), bezi paralelne
    na pozadi do docasnych souboru v /tmp (RAM), aby se jejich vystup nemichal s ostatnimi sekcemi.
    traffic: auto (nlbwmon, kdyz bezi, jinak conntrack) / nlbwmon / conntrack / off."""
    ok = [i for i, h in enumerate(pings) if valid_host(h)]  # indexy zustavaji (klice pingu podle poradi)
    lines = ["d=/tmp/owadmin-stats; mkdir -p $d"]
    for i in ok:
        lines.append(f"ping -c 1 -W 1 {shlex.quote(pings[i])} >$d/p{i} 2>&1 &")
    lines += ["echo @@dev; cat /proc/net/dev",
              "echo @@thermal; for z in /sys/class/thermal/thermal_zone*; do "
              "[ -r $z/temp ] && echo \"$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)\"; done",
              "echo @@load; cat /proc/loadavg"]
    nlbw = "o=$(nlbw -c json -g mac,ip 2>/dev/null)"
    ct = "echo @@ct; cat /proc/net/nf_conntrack 2>/dev/null"
    if traffic == "auto":
        lines.append(f"{nlbw}; if [ -n \"$o\" ]; then echo @@nlbw; echo \"$o\"; else {ct}; fi")
    elif traffic == "nlbwmon":
        lines.append(f"{nlbw}; echo @@nlbw; echo \"$o\"")
    elif traffic == "conntrack":
        lines.append(ct)
    lines.append("wait")
    for i in ok:
        lines.append(f"echo @@ping:{i}; cat $d/p{i} 2>/dev/null; rm -f $d/p{i}")
    return "\n".join(lines)


# ---------------------------------------------------------------- ping

@dataclass
class PingTarget:
    key: str
    name: str
    host: str
    kind: str              # internet / gateway / device / router
    hist: deque = field(default_factory=lambda: deque(maxlen=720))

    def add(self, ts: float, ms: float | None):
        self.hist.append((ts, ms))

    @property
    def last(self) -> float | None:
        return self.hist[-1][1] if self.hist else None

    @property
    def ok(self) -> bool:
        return bool(self.hist) and self.hist[-1][1] is not None

    def loss(self) -> float:
        if not self.hist:
            return 0.0
        return 100.0 * sum(1 for _, v in self.hist if v is None) / len(self.hist)

    def avg(self) -> float | None:
        vals = [v for _, v in self.hist if v is not None]
        return sum(vals) / len(vals) if vals else None

    def strip(self, n: int = 90) -> list[str]:
        """Poslednich n vysledku jako tridy pro pruh: ok / slow / lost."""
        return ["lost" if v is None else ("slow" if v >= PING_SLOW_MS else "ok") for _, v in list(self.hist)[-n:]]


# ---------------------------------------------------------------- provoz klientu (internet)

class Traffic:
    """Provoz klientu z nlbwmon (kumulativni soucty) nebo conntracku (citace spojeni) -> rychlost za okno."""

    def __init__(self):
        self.source = ""                      # nlbwmon / conntrack / ""
        self.samples: deque = deque()         # (ts, dt, {klic: (down, up)})
        self.meta: dict[str, dict] = {}       # klic -> {mac, ip}
        self.totals: dict[str, tuple[int, int]] = {}   # nlbwmon: soucet za obdobi (down, up)
        self._prev_nlbw: dict[str, tuple[int, int]] = {}
        self._prev_ct: dict[str, tuple[str, int, int]] | None = None
        self._prev_ts = 0.0

    def _push(self, ts: float, deltas: dict[str, tuple[int, int]], window: int):
        dt = ts - self._prev_ts if self._prev_ts else 0.0
        self._prev_ts = ts
        if dt > 0:
            self.samples.append((ts, dt, deltas))
        while self.samples and self.samples[0][0] < ts - window:
            self.samples.popleft()

    def _switch(self, source: str):
        if self.source != source:
            self.source = source
            self.samples.clear()
            self._prev_nlbw, self._prev_ct, self._prev_ts, self.totals = {}, None, 0.0, {}

    def feed_nlbw(self, ts: float, recs: dict[str, dict]):
        self._switch("nlbwmon")
        deltas = {}
        for k, r in recs.items():
            self.meta[k] = {"mac": r["mac"], "ip": (r["ips"] or [""])[0]}
            cur = (r["rx"], r["tx"])
            prev = self._prev_nlbw.get(k)
            if prev is not None:
                d = (cur[0] - prev[0], cur[1] - prev[1])
                if d[0] >= 0 and d[1] >= 0 and (d[0] or d[1]):
                    deltas[k] = d
            self.totals[k] = cur
        self._prev_nlbw = {k: (r["rx"], r["tx"]) for k, r in recs.items()}
        self._push(ts, deltas, WINDOW_NLBW)

    def feed_ct(self, ts: float, conns: dict[str, tuple[str, int, int]]):
        self._switch("conntrack")
        deltas: dict[str, list[int]] = {}
        if self._prev_ct is not None:
            for k, (ip, up, down) in conns.items():
                p = self._prev_ct.get(k)
                du, dd = (up - p[1], down - p[2]) if p else (up, down)  # nove spojeni od minula = cele
                if du < 0 or dd < 0:
                    continue
                if du or dd:
                    e = deltas.setdefault(ip, [0, 0])
                    e[0] += dd
                    e[1] += du
                self.meta.setdefault(ip, {"mac": "", "ip": ip})
        self._prev_ct = conns
        self._push(ts, {k: (v[0], v[1]) for k, v in deltas.items()}, WINDOW_CT)

    def rates(self) -> dict[str, tuple[float, float]]:
        """{klic: (down b/s, up b/s)} – prumer pres okno."""
        span = sum(dt for _, dt, _ in self.samples)
        if span <= 0:
            return {}
        acc: dict[str, list[int]] = {}
        for _, _, deltas in self.samples:
            for k, (d, u) in deltas.items():
                e = acc.setdefault(k, [0, 0])
                e[0] += d
                e[1] += u
        return {k: (v[0] * 8 / span, v[1] * 8 / span) for k, v in acc.items()}


# ---------------------------------------------------------------- hlavni stav statistik

class Stats:
    def __init__(self, interval: int = 5):
        self.interval = max(2, int(interval or 5))
        self.maxlen = max(10, HISTORY // self.interval)
        self.ifaces: dict[str, dict] = {}      # zarizeni -> {rx, tx, pps: Series, prev: (ts, rxb, rxp, txb, txp)}
        self.temps: list[tuple[str, float]] = []
        self.temp = Series(self.maxlen)
        self.load: float | None = None
        self.pings: dict[str, PingTarget] = {}
        self.traffic = Traffic()
        self.wifi: dict[tuple[str, str], tuple[float, float]] = {}    # (ap, mac) -> (down, up) b/s
        self._wifi_prev: dict[tuple[str, str], tuple[float, int, int]] = {}
        self.ports: dict[tuple[str, str], dict] = {}   # (switch, port) -> {down, up: Series, prev: (ts, in, out, bits)}
        self.router_ts = 0.0
        self.router_error = ""
        self.switch_errors: dict[str, str] = {}

    # ---- router
    def ping_target(self, key: str, name: str, host: str, kind: str) -> PingTarget:
        p = self.pings.get(key)
        if p is None or p.host != host:
            p = self.pings[key] = PingTarget(key, name, host, kind, deque(maxlen=self.maxlen))
        p.name, p.kind = name, kind
        return p

    def prune_pings(self, keys: set[str]):
        for k in list(self.pings):
            if k not in keys:
                del self.pings[k]

    def feed_router(self, ts: float, text: str, ping_keys: list[str], is_local=None):
        sec = parse_sections(text)
        for dev, (rxb, rxp, txb, txp) in parse_net_dev(sec.get("dev", "")).items():
            e = self.ifaces.setdefault(dev, {"rx": Series(self.maxlen), "tx": Series(self.maxlen),
                                             "pps": Series(self.maxlen), "prev": None})
            p = e["prev"]
            e["prev"] = (ts, rxb, rxp, txb, txp)
            if not p or ts <= p[0]:
                continue
            dt = ts - p[0]
            d = [counter_delta(p[i], v) for i, v in zip((1, 2, 3, 4), (rxb, rxp, txb, txp))]
            if None in d:
                continue
            e["rx"].add(ts, d[0] * 8 / dt)
            e["tx"].add(ts, d[2] * 8 / dt)
            e["pps"].add(ts, (d[1] + d[3]) / dt)
        self.temps = parse_thermal(sec.get("thermal", ""))
        t = cpu_temp(self.temps)
        if t is not None:
            self.temp.add(ts, t)
        self.load = parse_loadavg(sec.get("load", ""))
        for i, key in enumerate(ping_keys):
            p = self.pings.get(key)
            if p is not None:
                p.add(ts, parse_ping(sec.get(f"ping:{i}", "")))
        if "nlbw" in sec:
            recs = parse_nlbw(sec["nlbw"])
            if recs or sec["nlbw"].strip():
                self.traffic.feed_nlbw(ts, recs)
        elif "ct" in sec and is_local is not None:
            self.traffic.feed_ct(ts, parse_conntrack(sec["ct"], is_local))
        self.router_ts = ts
        self.router_error = ""

    def iface_rate(self, dev: str) -> tuple[float | None, float | None]:
        e = self.ifaces.get(dev)
        return (e["rx"].last, e["tx"].last) if e else (None, None)

    # ---- switche
    def feed_switch(self, ts: float, name: str, counters: dict[int, tuple[int, int, int]], ports: list[dict]):
        """counters: {ifIndex: (in, out, bits)}; ports: porty switche (num, ifindex) z posledniho nacteni."""
        for p in ports:
            c = counters.get(p.get("ifindex"))
            if c is None:
                continue
            e = self.ports.setdefault((name, p["num"]), {"down": Series(self.maxlen), "up": Series(self.maxlen), "prev": None})
            prev = e["prev"]
            e["prev"] = (ts, c[0], c[1], c[2])
            if not prev or ts <= prev[0]:
                continue
            dt = ts - prev[0]
            di, do = counter_delta(prev[1], c[0], c[2]), counter_delta(prev[2], c[1], c[2])
            if di is None or do is None:
                continue
            e["down"].add(ts, do * 8 / dt)   # out z portu = k zarizeni (download)
            e["up"].add(ts, di * 8 / dt)     # in do portu = od zarizeni (upload)
        self.switch_errors.pop(name, None)

    def port_rate(self, sw: str, num: str) -> tuple[float | None, float | None]:
        """(k zarizeni na portu, od zarizeni) v b/s."""
        e = self.ports.get((sw, str(num)))
        return (e["down"].last, e["up"].last) if e else (None, None)

    # ---- Wi-Fi
    def update_wifi(self, ts: float, aps):
        """Rychlost Wi-Fi klientu z rozdilu bajtu hostapd (rx = od klienta, tx = ke klientovi)."""
        cur, rates = {}, {}
        for ap in aps:
            for c in ap.clients:
                if c.get("rx_bytes") is None or c.get("tx_bytes") is None:
                    continue
                k = (ap.name, c["mac"])
                cur[k] = (ts, int(c["rx_bytes"]), int(c["tx_bytes"]))
                p = self._wifi_prev.get(k)
                if p and ts > p[0]:
                    dr, dtx = cur[k][1] - p[1], cur[k][2] - p[2]
                    if dr >= 0 and dtx >= 0:
                        rates[k] = (dtx * 8 / (ts - p[0]), dr * 8 / (ts - p[0]))
        self._wifi_prev, self.wifi = cur, rates

    # ---- vystupy pro stranky
    def top_clients(self, n: int, resolve) -> list[dict]:
        """resolve(mac, ip) -> {hostname, ip, mac}. Serazeno podle soucasne rychlosti (down + up)."""
        rows = []
        for k, (d, u) in self.traffic.rates().items():
            m = self.traffic.meta.get(k) or {}
            info = resolve(m.get("mac", ""), m.get("ip", ""))
            tot = self.traffic.totals.get(k)
            rows.append({"key": k, "down": d, "up": u, "mac": m.get("mac") or info.get("mac", ""),
                         "ip": m.get("ip") or info.get("ip", ""), "hostname": info.get("hostname", ""),
                         "total_down": tot[0] if tot else None, "total_up": tot[1] if tot else None})
        rows.sort(key=lambda r: -(r["down"] + r["up"]))
        return [r for r in rows if r["down"] + r["up"] > 0][:n]

    def busiest_ports(self, n: int = 10) -> list[dict]:
        rows = []
        for (sw, num), e in self.ports.items():
            d, u = e["down"].last, e["up"].last
            if d is None:
                continue
            rows.append({"switch": sw, "num": num, "down": d, "up": u, "series": e})
        rows.sort(key=lambda r: -(r["down"] + r["up"]))
        return rows[:n]


# ---------------------------------------------------------------- SVG (sparkline)

def spark(series: list, w: int = 160, h: int = 34, vmax: float | None = None, area: bool = True) -> dict:
    """Seznam rad hodnot -> {"paths": [d...], "max": vmax}. Prvni rada jako plocha, dalsi jako cary.
    Vsechny rady sdili osu Y (spolecne maximum)."""
    vals = [[v for v in s if v is not None] for s in series]
    top = vmax or max((max(v) for v in vals if v), default=0) or 1
    paths = []
    for k, v in enumerate(vals):
        if len(v) < 2:
            paths.append("")
            continue
        step = w / (len(v) - 1)
        pts = [(i * step, h - 1 - (h - 2) * min(1.0, x / top)) for i, x in enumerate(v)]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if k == 0 and area:
            d += f" L{w:.1f},{h} L0,{h} Z"
        paths.append(d)
    return {"paths": paths, "max": top, "w": w, "h": h}


def fmt_bps(v) -> str:
    """b/s -> '3.7 Mb/s', '850 kb/s'."""
    if v is None:
        return "–"
    v = float(v)
    if v >= 1e9:
        return f"{v / 1e9:.2f} Gb/s"
    if v >= 1e6:
        return f"{v / 1e6:.1f} Mb/s" if v < 1e8 else f"{v / 1e6:.0f} Mb/s"
    if v >= 1e3:
        return f"{v / 1e3:.0f} kb/s"
    return f"{v:.0f} b/s"


def short_bps(v) -> str:
    """Kratky tvar na mapu: '12M', '850k', '1.2G'."""
    if not v:
        return "0"
    if v >= 1e9:
        return f"{v / 1e9:.1f}G"
    if v >= 1e6:
        return f"{v / 1e6:.0f}M" if v >= 1e7 else f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}k"
    return f"{v:.0f}"


# ---------------------------------------------------------------- senzory do Home Assistantu

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "x"


def mbit(v) -> float | None:
    return None if v is None else round(v / 1e6, 3)


def ha_entities(st: Stats, router_name: str, ifaces: list[dict], top: list[dict], switches: list,
                internet_keys: list[str]) -> list[tuple[str, object, dict]]:
    """-> [(entity_id, stav, atributy)] pro REST API HA. ifaces: [{name, device, vlan, wan}] z routeru."""
    out: list[tuple[str, object, dict]] = []
    rate = {"unit_of_measurement": "Mbit/s", "device_class": "data_rate", "state_class": "measurement"}
    for i in ifaces:
        rx, tx = st.iface_rate(i["device"])
        if rx is None:
            continue
        e = st.ifaces[i["device"]]
        base = f"openwrt_admin_{slug(router_name)}_{slug(i['name'])}"
        nm = f"{router_name} {i['name'].upper() if i.get('wan') else i['name']}"
        out.append((f"sensor.{base}_rx", mbit(rx), {**rate, "friendly_name": f"{nm} RX", "icon": "mdi:download-network"}))
        out.append((f"sensor.{base}_tx", mbit(tx), {**rate, "friendly_name": f"{nm} TX", "icon": "mdi:upload-network"}))
        out.append((f"sensor.{base}_packets", round(e["pps"].last or 0), {
            "unit_of_measurement": "packets/s", "state_class": "measurement", "friendly_name": f"{nm} packets",
            "icon": "mdi:lan"}))
    if st.temp.last is not None:
        out.append((f"sensor.openwrt_admin_{slug(router_name)}_cpu_temperature", st.temp.last, {
            "unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement",
            "friendly_name": f"{router_name} CPU"}))
    if st.load is not None:
        out.append((f"sensor.openwrt_admin_{slug(router_name)}_load", st.load, {
            "state_class": "measurement", "friendly_name": f"{router_name} load", "icon": "mdi:cpu-64-bit"}))
    for p in st.pings.values():
        base = f"openwrt_admin_ping_{slug(p.name)}"
        out.append((f"binary_sensor.{base}", "on" if p.ok else "off", {
            "device_class": "connectivity", "friendly_name": f"Ping {p.name}", "host": p.host}))
        out.append((f"sensor.{base}", round(p.last, 1) if p.last is not None else "unavailable", {
            "unit_of_measurement": "ms", "device_class": "duration", "state_class": "measurement",
            "friendly_name": f"Ping {p.name}", "host": p.host, "loss_1h": round(p.loss(), 1), "icon": "mdi:timer-outline"}))
    if internet_keys:
        ok = any(st.pings[k].ok for k in internet_keys if k in st.pings)
        out.append(("binary_sensor.openwrt_admin_internet", "on" if ok else "off", {
            "device_class": "connectivity", "friendly_name": "Internet"}))
    out.append(("sensor.openwrt_admin_top_clients", len(top), {
        "friendly_name": "Internet – top clients", "icon": "mdi:account-network", "source": st.traffic.source,
        "clients": [{"name": r["hostname"] or r["mac"] or r["ip"], "ip": r["ip"], "mac": r["mac"],
                     "download": mbit(r["down"]), "upload": mbit(r["up"])} for r in top]}))
    for sw in switches:
        if not sw.online:
            continue
        for port in sw.ports:
            if not port["up"]:
                continue
            d, u = st.port_rate(sw.name, port["num"])
            if d is None:
                continue
            base = f"openwrt_admin_{slug(sw.name)}_port{slug(port['num'])}"
            nm = f"{sw.name} port {port['num']}" + (f" ({port['alias']})" if port.get("alias") else "")
            out.append((f"sensor.{base}_down", mbit(d), {**rate, "friendly_name": f"{nm} ↓", "icon": "mdi:download-network"}))
            out.append((f"sensor.{base}_up", mbit(u), {**rate, "friendly_name": f"{nm} ↑", "icon": "mdi:upload-network"}))
    return out


# ---------------------------------------------------------------- demo

def _prof(key: str) -> random.Random:
    return random.Random(zlib.crc32(key.encode()))


class DemoFeed:
    """Vymyslene, ale realisticky vypadajici vystupy routeru a citace switche (pro demo a screenshoty).
    Generuje text ve stejnem formatu jako skutecny router, takze se pouzivaji stejne parsery."""

    # zarizeni: (rx b/s, tx b/s, prubeh podle) – LAN kopiruje WAN (co prijde z internetu, odejde do LAN)
    DEVS = {"eth0": (25e6, 4e6, "eth0"), "br-lan": (3.4e6, 21e6, "eth0"), "br-lan.30": (0.4e6, 1.5e6, "br-lan.30"),
            "br-lan.10": (0.05e6, 0.2e6, "br-lan.10"), "eth1.100": (0.8e6, 2.5e6, "eth1.100"),
            "wg0": (0.1e6, 0.3e6, "wg0"), "lo": (0.01e6, 0.01e6, "lo")}

    def __init__(self):
        self.cnt: dict[str, list[float]] = {}
        self.port_cnt: dict[tuple[str, str], list[float]] = {}
        self.nlbw: dict[str, list[float]] = {}

    @staticmethod
    def _wave(ts: float, key: str) -> float:
        r = _prof(key)
        ph, per = r.random() * 6.28, 300 + r.random() * 900
        burst = 2.5 if math.sin(ts / 97 + ph) > 0.93 else 1.0
        return max(0.05, (0.6 + 0.4 * math.sin(ts / per * 6.28 + ph)) * burst * (0.85 + random.random() * 0.3))

    def router_text(self, ts: float, ping_hosts: list[str], dt: float, clients: list[tuple[str, str]]) -> str:
        lines = ["@@dev", "Inter-|   Receive |  Transmit", " face |bytes packets errs drop fifo frame compressed multicast|bytes packets"]
        waves: dict[str, float] = {}
        for dev, (rx, tx, wk) in self.DEVS.items():
            c = self.cnt.setdefault(dev, [1e9, 1e6, 3e8, 4e5])
            f = waves.setdefault(wk, self._wave(ts, wk))
            c[0] += rx * f * dt / 8
            c[2] += tx * f * dt / 8
            c[1] += rx * f * dt / 8 / 900
            c[3] += tx * f * dt / 8 / 700
            lines.append(f"{dev:>8}: {int(c[0])} {int(c[1])} 0 0 0 0 0 0 {int(c[2])} {int(c[3])} 0 0 0 0 0 0")
        lines += ["@@thermal", f"cpu-thermal {int((48 + 5 * math.sin(ts / 600)) * 1000)}", "@@load",
                  f"{0.1 + 0.15 * self._wave(ts, 'load'):.2f} 0.12 0.09 1/120 4242"]
        rows = []
        for mac, ip in clients:
            r = _prof(mac)
            base = r.choice([0.02e6, 0.1e6, 0.5e6, 2e6, 8e6, 25e6]) if r.random() > 0.35 else 0
            c = self.nlbw.setdefault(mac, [r.random() * 5e9, r.random() * 5e8])
            f = self._wave(ts, mac)
            c[0] += base * f * dt / 8
            c[1] += base * 0.15 * f * dt / 8
            rows.append([mac, ip, int(c[0]), int(c[1])])
        lines += ["@@nlbw", json.dumps({"columns": ["mac", "ip", "rx_bytes", "tx_bytes"], "data": rows})]
        for i, h in enumerate(ping_hosts):
            r = _prof(h)
            lost = random.random() < (0.03 if r.random() > 0.5 else 0.005)
            base = 0.6 if h.startswith(("192.168.", "10.")) else 4 + r.random() * 20
            ms = base * (0.8 + random.random() * 0.5) + (80 if random.random() < 0.02 else 0)
            lines += [f"@@ping:{i}", f"PING {h} ({h}): 56 data bytes"]
            lines += ([] if lost else [f"64 bytes from {h}: seq=0 ttl=57 time={ms:.3f} ms"])
            lines += ["", f"--- {h} ping statistics ---", f"1 packets transmitted, {0 if lost else 1} packets received"]
        return "\n".join(lines)

    def switch_counters(self, ts: float, name: str, ports: list[dict], dt: float) -> dict[int, tuple[int, int, int]]:
        out = {}
        for p in ports:
            if not p.get("up") or p.get("ifindex") is None:
                continue
            r = _prof(f"{name}:{p['num']}")
            base = min(r.choice([0.2e6, 1e6, 5e6, 20e6, 60e6]), (p.get("speed") or 1000) * 1e6 * 0.2)
            c = self.port_cnt.setdefault((name, p["num"]), [r.random() * 1e10, r.random() * 1e10])
            f = self._wave(ts, f"{name}:{p['num']}")
            c[0] += base * 0.3 * f * dt / 8
            c[1] += base * f * dt / 8
            out[p["ifindex"]] = (int(c[0]), int(c[1]), 64)
        return out
