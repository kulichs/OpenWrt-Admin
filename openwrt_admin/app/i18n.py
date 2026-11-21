"""Preklady weboveho rozhrani.

Kazdy jazyk je jeden soubor app/i18n/<kod>.json ({klic: text}). Novy jazyk = zkopirovat en.json,
prelozit hodnoty a pridat kod do PLURALS (pravidlo mnozneho cisla). Chybejici klic se vezme z en.json.
Text muze obsahovat {promenne}; mnozne cislo: hodnota je seznam tvaru, vybira se podle `n`.
"""
from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).parent / "i18n"
DEFAULT = "en"


def _plural_cs(n: int) -> int:
    return 0 if n == 1 else 1 if 2 <= n <= 4 else 2


def _plural_en(n: int) -> int:
    return 0 if n == 1 else 1


PLURALS = {"cs": _plural_cs, "en": _plural_en}

STRINGS: dict[str, dict] = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(DIR.glob("*.json"))}
LANGUAGES = sorted(STRINGS)
NAMES = {code: STRINGS[code].get("_language_name", code) for code in LANGUAGES}


def normalize(code: str | None) -> str:
    """'cs-CZ' / '"cs"' / 'en_GB' -> 'cs' / 'en'; nepodporovany -> ''."""
    c = (code or "").strip().strip('"').lower().replace("_", "-")
    if c in STRINGS:
        return c
    c = c.split("-")[0]
    return c if c in STRINGS else ""


def from_accept_language(header: str | None) -> str:
    for part in (header or "").split(","):
        lang = normalize(part.split(";")[0])
        if lang:
            return lang
    return ""


def translator(lang: str):
    table = STRINGS.get(lang, STRINGS[DEFAULT])
    fallback = STRINGS[DEFAULT]
    plural = PLURALS.get(lang, _plural_en)

    def t(key: str, n: int | None = None, **kw) -> str:
        v = table.get(key, fallback.get(key, key))
        if isinstance(v, list):
            if not isinstance(n, int) and v:
                n = 0
            v = v[min(plural(n), len(v) - 1)] if v else key
        if n is not None:
            kw.setdefault("n", n)
        try:
            return v.format(**kw) if kw else v
        except (KeyError, IndexError, ValueError):
            return v

    return t
