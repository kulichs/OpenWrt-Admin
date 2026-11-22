"""Radia AP: skutecny stav z `iwinfo` a `iw survey`, vytizeni kanalu, upozorneni a mapa kanalu.

Zdroje (jen cteni, standardni soucast OpenWrt):
- `iwinfo` bez parametru: kazde Wi-Fi rozhrani se skutecnym kanalem, sirkou, vykonem, sumem, hardwarem a jmenem phy,
- `iw dev <if> survey dump`: citace casu kanalu (active/busy/receive/transmit) – vytizeni = rozdil busy / rozdil active
  mezi dvema nactenimi (citace jsou od startu). Kdyz `iw` chybi, vytizeni se proste nezobrazi.
Parsery jsou ciste funkce (parse_*), upozorneni a mapa kanalu taky (bez I/O).
"""
from __future__ import annotations

import re

# kanaly 5 GHz (20 MHz) v poradi; skupiny pro 40/80/160 MHz
CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165]
GROUPS5 = {
    40: [(36, 40), (44, 48), (52, 56), (60, 64), (100, 104), (108, 112), (116, 120), (124, 128), (132, 136), (140, 144),
         (149, 153), (157, 161)],
    80: [(36, 40, 44, 48), (52, 56, 60, 64), (100, 104, 108, 112), (116, 120, 124, 128), (132, 136, 140, 144),
         (149, 153, 157, 161)],
    160: [(36, 40, 44, 48, 52, 56, 60, 64), (100, 104, 108, 112, 116, 120, 124, 128)],
}
NON_OVERLAP_24 = {1, 6, 11}

# radek, kterym zacina rozhrani v `iwinfo`: 'phy0-ap0  ESSID: "x"' (nekdy 'wlan0     ESSID: unknown')
IW_HEAD = re.compile(r'^(\S+)\s+ESSID:\s*(.*)$')
IW_CHAN = re.compile(r"Channel:\s*(\d+)\s*\(([\d.]+)\s*GHz\)")
IW_HT = re.compile(r"HT Mode:\s*(\S+)")
IW_TX = re.compile(r"Tx-Power:\s*(-?\d+)\s*dBm")
IW_NOISE = re.compile(r"Noise:\s*(-?\d+)\s*dBm")
IW_HW = re.compile(r"Hardware:\s*(.+)$", re.M)
IW_PHY = re.compile(r"PHY name:\s*(\S+)")
IW_BSSID = re.compile(r"Access Point:\s*([0-9A-Fa-f:]{17})")

RADIO_CMD = ("iwinfo 2>/dev/null; echo '@@survey'; seen=''; for n in /sys/class/net/*; do f=$n/phy80211/name; "
             "[ -r $f ] || continue; ph=$(cat $f); case \" $seen \" in *\" $ph \"*) continue;; esac; "
             "i=${n##*/}; out=$(iw dev $i survey dump 2>/dev/null | awk '/frequency/{u=/in use/} u'); "
             "[ -n \"$out\" ] || continue; seen=\"$seen $ph\"; echo \"== $ph $i\"; echo \"$out\"; done")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_iwinfo(text: str) -> dict[str, dict]:
    """`iwinfo` -> {ifname: {essid, channel, freq (MHz), htmode, txpower, noise, hw, phy}}."""
    out: dict[str, dict] = {}
    cur = None
    buf: list[str] = []

    def flush():
        if cur is None:
            return
        b = "\n".join(buf)
        e = {"essid": cur[1], "channel": None, "freq": None, "htmode": "", "txpower": None, "noise": None,
             "hw": "", "phy": "", "bssid": ""}
        m = IW_CHAN.search(b)
        if m:
            e["channel"], e["freq"] = int(m.group(1)), round(float(m.group(2)) * 1000)
        m = IW_HT.search(b)
        if m and m.group(1).lower() != "unknown":
            e["htmode"] = m.group(1)
        m = IW_TX.search(b)
        e["txpower"] = _int(m.group(1)) if m else None
        m = IW_NOISE.search(b)
        e["noise"] = _int(m.group(1)) if m else None
        m = IW_HW.search(b)
        if m:
            hw = m.group(1).strip()
            br = re.search(r"\[(.+)\]", hw)  # 'embedded [Atheros AR9340]' -> 'Atheros AR9340'
            e["hw"] = br.group(1) if br else hw
        m = IW_PHY.search(b)
        e["phy"] = m.group(1) if m else ""
        m = IW_BSSID.search(b)
        e["bssid"] = m.group(1).lower() if m else ""
        out[cur[0]] = e

    for line in (text or "").splitlines():
        m = IW_HEAD.match(line)
        if m:
            flush()
            cur, buf = (m.group(1), m.group(2).strip().strip('"')), []
        elif cur is not None:
            buf.append(line)
    flush()
    return out


SURVEY_RE = {
    "freq": re.compile(r"frequency:\s*(\d+)"),
    "noise": re.compile(r"noise:\s*(-?\d+)"),
    "active": re.compile(r"channel active time:\s*(\d+)"),
    "busy": re.compile(r"channel busy time:\s*(\d+)"),
    "rx": re.compile(r"channel receive time:\s*(\d+)"),
    "tx": re.compile(r"channel transmit time:\s*(\d+)"),
}


def parse_survey(text: str) -> dict[str, dict]:
    """Vystup RADIO_CMD za '@@survey' -> {phy: {ifname, freq, noise, active, busy, rx, tx}} (kanal v pouziti)."""
    out: dict[str, dict] = {}
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("== "):
            p = line[3:].split()
            if len(p) >= 2:
                cur = out.setdefault(p[0], {"ifname": p[1]})
            continue
        if cur is None:
            continue
        for k, rx in SURVEY_RE.items():
            m = rx.search(line)
            if m and k not in cur:
                cur[k] = int(m.group(1))
    return {k: v for k, v in out.items() if "active" in v and "busy" in v}


def split_radio_output(text: str) -> tuple[str, str]:
    a, _, b = (text or "").partition("@@survey")
    return a, b


def width_of(htmode: str) -> int:
    """'HT20' -> 20, 'VHT80' -> 80, 'HE160' -> 160, 'NOHT' -> 20."""
    m = re.search(r"(\d+)", htmode or "")
    return int(m.group(1)) if m else 20


def is_dfs(ch: int | None) -> bool:
    return bool(ch) and 52 <= ch <= 144


def occupied(band: str, ch: int | None, htmode: str) -> list[int]:
    """20MHz kanaly, ktere radio zabira. 2,4 GHz: kanaly (po 5 MHz) v sirce pasma; 5 GHz: skupina podle sirky."""
    if not ch:
        return []
    w = width_of(htmode)
    if band.startswith("2"):
        if w >= 40:  # HT40+/- – bez znalosti strany bereme +-4 kanaly
            return list(range(max(1, ch - 4), min(14, ch + 4) + 1))
        return list(range(max(1, ch - 2), min(14, ch + 2) + 1))
    if band.startswith("5"):
        for g in GROUPS5.get(w, []):
            if ch in g:
                return list(g)
        return [ch]
    return [ch]


def overlap(a: dict, b: dict) -> bool:
    if a["band"] != b["band"] or not a.get("channel") or not b.get("channel"):
        return False
    if a["band"].startswith("2"):
        # 2,4 GHz: kanaly 20 MHz se prekryvaji, kdyz jsou blize nez 5 kanalu
        reach = 4 + (2 if width_of(a["htmode"]) >= 40 else 0) + (2 if width_of(b["htmode"]) >= 40 else 0)
        return abs(a["channel"] - b["channel"]) <= reach
    return bool(set(occupied(a["band"], a["channel"], a["htmode"])) & set(occupied(b["band"], b["channel"], b["htmode"])))


def radio_rows(aps, util: dict | None = None) -> list[dict]:
    """Radia vsech online AP jako ploche radky: nastaveni z uci/netifd + skutecnost z iwinfo + vytizeni."""
    util = util or {}
    rows = []
    for ap in aps:
        if not ap.online:
            continue
        for r in ap.radios:
            ch = r.get("act_channel") or _int(r.get("channel"))
            ht = r.get("act_htmode") or str(r.get("htmode") or "")
            rows.append({
                "ap": ap.name, "radio": r["name"], "band": r.get("band", ""), "up": r.get("up"), "disabled": r.get("disabled"),
                "cfg_channel": str(r.get("channel", "")), "channel": ch, "htmode": ht, "cfg_htmode": str(r.get("htmode") or ""),
                "cfg_txpower": str(r.get("txpower", "")), "txpower": r.get("act_txpower"), "country": r.get("country", ""),
                "noise": r.get("noise"), "hw": r.get("hw", ""), "phy": r.get("phy", ""), "freq": r.get("freq"),
                "clients": sum(1 for c in ap.clients if c.get("ifname") in set(r.get("ifnames") or [])),
                "util": util.get((ap.name, r["name"])), "dfs": is_dfs(ch) if r.get("band", "").startswith("5") else False,
            })
    return rows


def warnings(rows: list[dict]) -> list[dict]:
    """Upozorneni: [{level: warn|info, key: i18n klic, args: {...}, ap, radio}]."""
    out: list[dict] = []

    def w(level, key, row=None, **args):
        out.append({"level": level, "key": key, "args": args, "ap": row["ap"] if row else "", "radio": row["radio"] if row else ""})

    act = [r for r in rows if r["up"] and r["channel"]]
    for i, a in enumerate(act):
        for b in act[i + 1:]:
            if a["ap"] != b["ap"] and overlap(a, b):
                same = a["channel"] == b["channel"]
                w("warn", "radio.w_same" if same else "radio.w_overlap", a, a=a["ap"], b=b["ap"],
                  ca=a["channel"], cb=b["channel"], band=a["band"])
    for r in rows:
        if r["disabled"] or not r["up"]:
            w("info", "radio.w_off", r, ap=r["ap"], radio=r["radio"])
            continue
        b24 = r["band"].startswith("2")
        if r["cfg_channel"] in ("auto", "0", ""):
            w("info", "radio.w_auto", r, ap=r["ap"], radio=r["radio"], ch=r["channel"] or "?")
        if b24 and r["channel"] and r["channel"] not in NON_OVERLAP_24:
            w("warn", "radio.w_24_channel", r, ap=r["ap"], ch=r["channel"])
        if b24 and width_of(r["htmode"]) >= 40:
            w("warn", "radio.w_24_wide", r, ap=r["ap"], ht=r["htmode"])
        if r["band"].startswith("5") and width_of(r["htmode"]) == 20:
            w("info", "radio.w_5_narrow", r, ap=r["ap"], ht=r["htmode"] or "20 MHz")
        if r["dfs"]:
            w("info", "radio.w_dfs", r, ap=r["ap"], ch=r["channel"])
        if r["util"] is not None and r["util"] >= 50:
            w("warn", "radio.w_busy", r, ap=r["ap"], band=r["band"], p=round(r["util"]))
        if r["noise"] is not None and r["noise"] > -85:
            w("warn", "radio.w_noise", r, ap=r["ap"], band=r["band"], n=r["noise"])
    # 2,4 GHz silnejsi nez 5 GHz na stejnem AP -> klienti se drzi 2,4 GHz
    by_ap: dict[str, dict] = {}
    for r in rows:
        if r["up"] and r["txpower"] is not None:
            by_ap.setdefault(r["ap"], {})[r["band"][:1]] = r
    for ap, d in by_ap.items():
        if "2" in d and "5" in d and d["2"]["txpower"] > d["5"]["txpower"]:
            w("info", "radio.w_power_balance", d["2"], ap=ap, p24=d["2"]["txpower"], p5=d["5"]["txpower"])
    countries = {r["country"] for r in rows if r["country"]}
    if len(countries) > 1:
        w("warn", "radio.w_country_mixed", None, c=", ".join(sorted(countries)))
    if any(not r["country"] for r in rows):
        w("info", "radio.w_country_missing", None, aps=", ".join(sorted({r["ap"] for r in rows if not r["country"]})))
    return out


PALETTE = ["#03a9f4", "#e67e22", "#8e44ad", "#16a085", "#c0392b", "#2c7be5", "#b7950b"]


def channel_map(rows: list[dict], band: str, cells: list[dict] | None = None) -> dict:
    """Mapa kanalu pro SVG: osa kanalu, obdelniky radii (zabrane 20MHz kanaly), radky = AP;
    s vysledky skenu (cells) i sloupce obsazenosti kanalu cizimi sitemi."""
    rr = [r for r in rows if r["band"] == band and r["channel"] and r["up"]]
    cells = [c for c in (cells or []) if c["band"] == band]
    if band.startswith("2"):
        axis = list(range(1, 14))
    else:
        used = {c for r in rr for c in occupied(band, r["channel"], r["htmode"])}
        used |= {x for c in cells for x in cell_occupied(c)}
        axis = [c for c in CH5 if c <= 64 or c in used or c >= 149] if not any(100 <= c <= 144 for c in used) else CH5
    if not rr and not cells:
        return {"axis": axis, "bars": [], "band": band}
    col = 34 if band.startswith("2") else 26
    x0, row_h = 120, 22
    bars = []
    for k, r in enumerate(sorted(rr, key=lambda r: (r["ap"], r["radio"]))):
        occ = [c for c in occupied(band, r["channel"], r["htmode"]) if c in axis]
        if not occ:
            continue
        i1, i2 = axis.index(min(occ)), axis.index(max(occ))
        bars.append({"ap": r["ap"], "radio": r["radio"], "channel": r["channel"], "htmode": r["htmode"],
                     "x": x0 + i1 * col + 2, "w": (i2 - i1 + 1) * col - 4, "y": 28 + k * row_h,
                     "cx": x0 + axis.index(r["channel"]) * col + col / 2 if r["channel"] in axis else None,
                     "color": PALETTE[k % len(PALETTE)], "dfs": r["dfs"]})
    ticks = [{"ch": c, "x": x0 + i * col + col / 2, "dfs": band.startswith("5") and is_dfs(c),
              "best": band.startswith("2") and c in NON_OVERLAP_24} for i, c in enumerate(axis)]
    height = 36 + len(bars) * row_h
    occ_bars = []
    if cells:
        occ = occupancy(cells, band, axis)
        top = max((e["score"] for e in occ.values()), default=0) or 1
        oh, y0 = 46, height + 6
        for i, c in enumerate(axis):
            e = occ[c]
            h = round(oh * e["score"] / top) if e["score"] else 0
            occ_bars.append({"ch": c, "x": x0 + i * col + 3, "w": col - 6, "y": y0 + oh - h, "h": h, "count": e["count"],
                             "best": e["best"], "ty": y0 + oh + 12, "tx": x0 + i * col + col / 2,
                             "cls": "bad" if (e["best"] or -100) >= -65 else ("mid" if (e["best"] or -100) >= -80 else "low")})
        height = y0 + oh + 18
    return {"axis": axis, "ticks": ticks, "bars": bars, "band": band, "col": col, "x0": x0, "occ": occ_bars,
            "occ_y": height - 64 + 6 if cells else 0, "width": x0 + len(axis) * col + 10, "height": height}


# ---------------------------------------------------------------- sken okoli (iwinfo <if> scan)

IFNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
SC_CELL = re.compile(r"^Cell\s+\d+\s+-\s+Address:\s*([0-9A-Fa-f:]{17})", re.M)
SC_ESSID = re.compile(r'ESSID:\s*(?:"(.*)"|(\S+))')
SC_CHAN = re.compile(r"Channel:\s*(\d+)")
SC_FREQ = re.compile(r"Frequency:\s*([\d.]+)\s*GHz")
SC_SIG = re.compile(r"Signal:\s*(-?\d+)\s*dBm")
SC_ENC = re.compile(r"Encryption:\s*(.+)")
SC_OFF = re.compile(r"Secondary Channel Offset:\s*(above|below|no secondary)")
SC_HTW = re.compile(r"HT Operation:.*?Channel Width:\s*([^\n]+)", re.S)
SC_VHTW = re.compile(r"VHT Operation:.*?Channel Width:\s*([^\n]+)", re.S)


def scan_cmd(ifname: str) -> str:
    if not IFNAME_RE.match(ifname or ""):
        raise ValueError("bad ifname")
    return f"iwinfo {ifname} scan 2>&1"


def _cell_width(block: str) -> tuple[int, str]:
    """-> (sirka v MHz, posun sekundarniho kanalu above/below/'')."""
    off = SC_OFF.search(block)
    off = off.group(1) if off and off.group(1) != "no secondary" else ""
    vht = block.split("VHT Operation:", 1)[1] if "VHT Operation:" in block else ""
    m = re.search(r"Channel Width:\s*([^\n]+)", vht)
    if m and "160" in m.group(1):
        return 160, off
    if m and "80" in m.group(1):
        return 80, off
    ht = block.split("HT Operation:", 1)[1].split("VHT Operation:", 1)[0] if "HT Operation:" in block else ""
    m = re.search(r"Channel Width:\s*([^\n]+)", ht)
    if m and "40" in m.group(1) and off:
        return 40, off
    return 20, ""


def parse_scan(text: str) -> list[dict]:
    """`iwinfo <if> scan` -> [{bssid, essid, channel, freq, band, signal, enc, width, offset}] serazeno podle signalu."""
    out = []
    starts = [m for m in SC_CELL.finditer(text or "")]
    for k, m in enumerate(starts):
        block = text[m.end(): starts[k + 1].start() if k + 1 < len(starts) else len(text)]
        ch = SC_CHAN.search(block)
        if not ch:
            continue
        ch = int(ch.group(1))
        es = SC_ESSID.search(block)
        essid = (es.group(1) if es and es.group(1) is not None else (es.group(2) if es else "")) or ""
        if essid == "unknown" and not (es and es.group(1) is not None):
            essid = ""
        fq = SC_FREQ.search(block)
        sig = SC_SIG.search(block)
        enc = SC_ENC.search(block)
        w, off = _cell_width(block)
        out.append({"bssid": m.group(1).lower(), "essid": essid, "channel": ch,
                    "freq": round(float(fq.group(1)) * 1000) if fq else None,
                    "band": "2,4 GHz" if ch <= 14 else ("6 GHz" if fq and float(fq.group(1)) >= 5.925 else "5 GHz"),
                    "signal": int(sig.group(1)) if sig else None, "enc": enc.group(1).strip() if enc else "",
                    "width": w, "offset": off})
    out.sort(key=lambda c: -(c["signal"] if c["signal"] is not None else -200))
    return out


def cell_occupied(c: dict) -> list[int]:
    """20MHz kanaly, ktere cizi sit zabira (podle sirky a posunu sekundarniho kanalu)."""
    ch, w = c["channel"], c["width"]
    if c["band"].startswith("2"):
        if w >= 40:
            lo, hi = (ch - 2, ch + 6) if c["offset"] == "above" else (ch - 6, ch + 2)
        else:
            lo, hi = ch - 2, ch + 2
        return list(range(max(1, lo), min(14, hi) + 1))
    return occupied(c["band"], ch, f"HT{w}")


def occupancy(cells: list[dict], band: str, axis: list[int]) -> dict[int, dict]:
    """Obsazenost kanalu cizimi sitemi: {kanal: {count, best (nejsilnejsi dBm), score}}.
    score = soucet (signal + 95) pres site, ktere kanal zabiraji – cim vic a silnejsich, tim hur."""
    occ = {c: {"count": 0, "best": None, "score": 0.0} for c in axis}
    for c in cells:
        if c["band"] != band or c.get("own") or c["signal"] is None:
            continue
        for ch in cell_occupied(c):
            if ch in occ:
                e = occ[ch]
                e["count"] += 1
                e["best"] = c["signal"] if e["best"] is None else max(e["best"], c["signal"])
                e["score"] += max(0, c["signal"] + 95)
    return occ


def best_channels(band: str, htmode: str, occ: dict[int, dict], dfs_ok: bool = False) -> list[tuple[int, float]]:
    """Kandidati na kanal pro dane radio serazene podle obsazenosti: 2,4 GHz jen 1/6/11, 5 GHz podle sirky
    (prvni kanal skupiny), bez DFS, kdyz dfs_ok neni."""
    if band.startswith("2"):
        cands = [1, 6, 11]
    else:
        w = width_of(htmode)
        groups = GROUPS5.get(w) or [(c,) for c in CH5]
        # bez DFS jen 36–48 (UNII-1); 149–165 je v EU jen pro SRD s nizkym vykonem (25 mW) – nedoporucovat
        cands = [g[0] for g in groups if g[-1] <= 144 and (dfs_ok or not any(is_dfs(c) for c in g))]
    out = []
    for c in cands:
        chans = occupied(band, c, htmode if not band.startswith("2") else "HT20")
        out.append((c, sum(occ.get(x, {}).get("score", 0.0) for x in chans)))
    return sorted(out, key=lambda x: (x[1], x[0]))


def scan_warnings(rows: list[dict], scans: dict) -> list[dict]:
    """Upozorneni ze skenu: silne cizi site na kanalu radia a lepsi volny kanal."""
    out = []
    for r in rows:
        sc = scans.get((r["ap"], r["radio"]))
        if not sc or not sc.get("cells") or not r["up"] or not r["channel"]:
            continue
        mine = set(occupied(r["band"], r["channel"], r["htmode"]))
        near = [c for c in sc["cells"] if not c.get("own") and c["band"] == r["band"] and mine & set(cell_occupied(c))]
        strong = [c for c in near if c["signal"] is not None and c["signal"] >= -70]
        if strong:
            c = strong[0]
            out.append({"level": "warn", "key": "radio.w_neighbor", "ap": r["ap"], "radio": r["radio"],
                        "args": {"ap": r["ap"], "band": r["band"], "ch": r["channel"], "n": len(near),
                                 "name": c["essid"] or c["bssid"], "sig": c["signal"]}})
        axis = list(range(1, 14)) if r["band"].startswith("2") else CH5
        occ = occupancy(sc["cells"], r["band"], axis)
        cand = best_channels(r["band"], r["htmode"], occ)
        cur = sum(occ.get(x, {}).get("score", 0.0) for x in mine)
        if cand and cand[0][0] != r["channel"] and cand[0][1] < cur * 0.5:
            out.append({"level": "info", "key": "radio.w_better", "ap": r["ap"], "radio": r["radio"],
                        "args": {"ap": r["ap"], "band": r["band"], "ch": r["channel"], "best": cand[0][0]}})
    return out
