"""Historie Wi-Fi klientu a roaming mezi AP (SQLite v datech aplikace).

Uklada se po USECICH: klient (MAC) byl na AP / rozhrani (SSID + pasmo) od-do, se signalem (prvni, posledni, min, max).
Zacatek useku se pocita z `connected_time` (ted - pripojeno), takze je presny i pri cteni jednou za 30 s; kdyz se
`connected_time` mezi ctenimi vynuluje (A -> B -> A), vznikne novy usek. Klienti na AP, ktere se zrovna nepodarilo
precist, se neukoncuji. Starsi useky nez `days` se jednou denne mazou.

Z useku se pocita casova osa, prechody (roaming: dalsi usek na jinem AP do ROAM_GAP s), zmeny pasma na stejnem AP,
pripojeni / odpojeni a „skakani“ (A -> B -> A behem PINGPONG s).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

ROAM_GAP = 90        # s – dalsi usek do teto doby = prechod (ne odpojeni)
PINGPONG = 600       # s – navrat na puvodni AP do teto doby = skakani
SLACK = 45           # s – tolerance pri navazovani na otevreny usek
STALE = 600          # s – otevreny usek bez cteni dele nez toto se na ose konci poslednim ctenim (AP offline)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seg (
  id INTEGER PRIMARY KEY, mac TEXT NOT NULL, ap TEXT NOT NULL, ifname TEXT, ssid TEXT, band TEXT,
  start REAL NOT NULL, last REAL NOT NULL, end REAL,
  sig_first INTEGER, sig_last INTEGER, sig_min INTEGER, sig_max INTEGER);
CREATE INDEX IF NOT EXISTS seg_mac ON seg(mac, start);
CREATE INDEX IF NOT EXISTS seg_open ON seg(end);
CREATE INDEX IF NOT EXISTS seg_time ON seg(start, end);
"""


class History:
    def __init__(self, path: Path | str | None, days: int):
        self.days = days
        self.enabled = days > 0
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False) if self.enabled else None
        if self.db:
            self.db.executescript(SCHEMA)
            self.db.commit()
        self.cleaned = 0.0

    # ------------------------------------------------------------ zapis
    def observe(self, now: float, clients: list[dict], online_aps: set[str]):
        """Jedno cteni: clients = [{mac, ap, ifname, ssid, band, signal, connected}] ze vsech AP, ktera odpovedela."""
        if not self.db:
            return
        cur = {}
        for c in clients:
            m = (c.get("mac") or "").lower()
            if m and c.get("ap"):
                cur[m] = c
        with self.lock:
            q = self.db.execute("SELECT id, mac, ap, ifname, start, last, sig_min, sig_max FROM seg WHERE end IS NULL")
            open_by_mac: dict[str, tuple] = {}
            for row in q.fetchall():
                open_by_mac[row[1]] = row
            for mac, row in open_by_mac.items():
                sid, _, ap, ifname, start, last, smin, smax = row
                c = cur.get(mac)
                if c is None:
                    if ap in online_aps:                   # AP odpovedel a klient tam neni -> odpojen / jinde
                        self.db.execute("UPDATE seg SET end=? WHERE id=?", (last, sid))
                    continue
                began = now - float(c["connected"]) if c.get("connected") is not None else None
                same = c["ap"] == ap and (c.get("ifname") or "") == (ifname or "")
                if same and (began is None or began <= last + SLACK):
                    sig = c.get("signal")
                    self.db.execute(
                        "UPDATE seg SET last=?, sig_last=COALESCE(?, sig_last), sig_min=MIN(COALESCE(sig_min, ?), COALESCE(?, sig_min)),"
                        " sig_max=MAX(COALESCE(sig_max, ?), COALESCE(?, sig_max)) WHERE id=?",
                        (now, sig, sig, sig, sig, sig, sid))
                    cur.pop(mac)
                else:
                    end = last if began is None else min(max(began, start), now)
                    self.db.execute("UPDATE seg SET end=? WHERE id=?", (max(end, start), sid))
            for mac, c in cur.items():
                began = now - float(c["connected"]) if c.get("connected") is not None else now
                prev_end = self.db.execute("SELECT MAX(COALESCE(end, last)) FROM seg WHERE mac=?", (mac,)).fetchone()[0]
                if prev_end and began < prev_end:
                    began = prev_end
                sig = c.get("signal")
                self.db.execute(
                    "INSERT INTO seg (mac, ap, ifname, ssid, band, start, last, sig_first, sig_last, sig_min, sig_max)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (mac, c["ap"], c.get("ifname") or "", c.get("ssid") or "", c.get("band") or "", min(began, now), now,
                     sig, sig, sig, sig))
            self.db.commit()
            if now - self.cleaned > 86400:
                self.cleaned = now
                self.db.execute("DELETE FROM seg WHERE end IS NOT NULL AND end < ?", (now - self.days * 86400,))
                self.db.commit()

    # ------------------------------------------------------------ cteni
    def segments(self, since: float, now: float, mac: str | None = None) -> dict[str, list[dict]]:
        if not self.db:
            return {}
        sql = ("SELECT mac, ap, ifname, ssid, band, start, COALESCE(end, last), end IS NULL, sig_first, sig_last, sig_min, sig_max"
               " FROM seg WHERE COALESCE(end, last) >= ? AND start <= ?")
        args: list = [since, now]
        if mac:
            sql += " AND mac=?"
            args.append(mac.lower())
        sql += " ORDER BY mac, start"
        out: dict[str, list[dict]] = {}
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        for r in rows:
            live = bool(r[7]) and r[6] >= now - STALE        # otevreny a nedavno potvrzeny
            out.setdefault(r[0], []).append({
                "ap": r[1], "ifname": r[2], "ssid": r[3], "band": r[4], "start": r[5],
                "end": now if live else r[6], "open": live, "sig_first": r[8], "sig_last": r[9],
                "sig_min": r[10], "sig_max": r[11]})
        return out

    def oldest(self) -> float | None:
        if not self.db:
            return None
        with self.lock:
            return self.db.execute("SELECT MIN(start) FROM seg").fetchone()[0]


# ---------------------------------------------------------------- vyhodnoceni

def events(segs: list[dict]) -> list[dict]:
    """Udalosti z useku jednoho klienta: connect, roam, band, disconnect (od nejnovejsi)."""
    out = []
    for i, s in enumerate(segs):
        prev = segs[i - 1] if i else None
        if prev and s["start"] - prev["end"] <= ROAM_GAP:
            kind = "band" if prev["ap"] == s["ap"] else "roam"
            out.append({"t": s["start"], "kind": kind, "from": prev, "to": s})
        else:
            out.append({"t": s["start"], "kind": "connect", "to": s})
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if not s["open"] and (not nxt or nxt["start"] - s["end"] > ROAM_GAP):
            out.append({"t": s["end"], "kind": "disconnect", "from": s})
    return sorted(out, key=lambda e: -e["t"])


def summary(segs: list[dict], since: float, now: float) -> dict:
    """Pro jednoho klienta: prechody, skakani, podil casu na AP, prumerny a nejhorsi signal."""
    roams = pingpong = bands = 0
    for i in range(1, len(segs)):
        a, b = segs[i - 1], segs[i]
        if b["start"] - a["end"] > ROAM_GAP:
            continue
        if a["ap"] != b["ap"]:
            roams += 1
            if i >= 2:
                z = segs[i - 2]
                if z["ap"] == b["ap"] and a["start"] - z["end"] <= ROAM_GAP and b["start"] - z["end"] <= PINGPONG:
                    pingpong += 1
        elif a.get("band") != b.get("band"):
            bands += 1
    share: dict[str, float] = {}
    total = 0.0
    sigs = []
    for s in segs:
        d = max(0.0, min(s["end"], now) - max(s["start"], since))
        share[s["ap"]] = share.get(s["ap"], 0.0) + d
        total += d
        if s["sig_min"] is not None:
            sigs.append(s["sig_min"])
    return {"roams": roams, "pingpong": pingpong, "bands": bands, "sessions": len(segs), "online": total,
            "share": sorted(((ap, d / total if total else 0) for ap, d in share.items()), key=lambda x: -x[1]),
            "worst": min(sigs) if sigs else None, "aps": len(share), "open": bool(segs and segs[-1]["open"])}


def demo_fill(h: "History", now: float, aps: list[str], clients: list[dict]):
    """Demo: 24 h vymyslene historie – telefony prechazi mezi AP, jeden „skace“, IoT stoji."""
    if not h.db or not aps:
        return
    import random
    rnd = random.Random(7)
    a1, a2 = aps[0], aps[1] if len(aps) > 1 else aps[0]
    with h.lock:
        for k, c in enumerate(clients[:12]):
            mac, t = c["mac"], now - 24 * 3600
            mode = "jumpy" if k == 0 else "roamer" if k in (1, 2, 3) else "static"
            ap = a2 if c.get("ap") == a1 else a1
            rows = []
            while t < now - 60:
                if mode == "static":
                    dur = 24 * 3600
                elif mode == "jumpy":
                    ago = (now - t) / 3600          # skace jen v jednom okne (5.–3,5 h zpet), jinak normalne
                    dur = rnd.uniform(60, 300) if 3.5 <= ago <= 5 else rnd.uniform(1800, 5400)
                else:
                    dur = rnd.uniform(1200, 7200)
                end = min(t + dur, now)
                band = "5 GHz" if (mode != "static" and rnd.random() > 0.35) else "2.4 GHz"
                sig = int(rnd.uniform(-78, -48))
                rows.append([mac, ap, "phy0-ap0", c.get("ssid") or "HomeWiFi", band, t, end, end, sig, sig + 3, sig - 6, sig + 8])
                if end >= now - 1:
                    break
                gap = rnd.uniform(5, 40) if mode != "roamer" or rnd.random() > 0.2 else rnd.uniform(600, 3600)
                t = end + gap
                ap = a2 if ap == a1 else a1
            if rows:                                   # posledni usek = AP a pasmo, kde klient v demu je ted
                rows[-1][1], rows[-1][4], rows[-1][7] = c.get("ap") or a1, c.get("band") or rows[-1][4], None
                rows[-1][3] = c.get("ssid") or rows[-1][3]
            for r in rows:
                h.db.execute(
                    "INSERT INTO seg (mac, ap, ifname, ssid, band, start, last, end, sig_first, sig_last, sig_min, sig_max)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r)
        h.db.commit()
