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
             "hw": "", "phy": ""}
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


def channel_map(rows: list[dict], band: str) -> dict:
    """Mapa kanalu pro SVG: osa kanalu a obdelniky radii (zabrane 20MHz kanaly), radky = AP."""
    rr = [r for r in rows if r["band"] == band and r["channel"] and r["up"]]
    if band.startswith("2"):
        axis = list(range(1, 14))
    else:
        used = {c for r in rr for c in occupied(band, r["channel"], r["htmode"])}
        axis = [c for c in CH5 if c <= 64 or c in used or c >= 149] if not any(100 <= c <= 144 for c in used) else CH5
    if not rr:
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
    return {"axis": axis, "ticks": ticks, "bars": bars, "band": band, "col": col, "x0": x0,
            "width": x0 + len(axis) * col + 10, "height": 36 + len(bars) * row_h}
