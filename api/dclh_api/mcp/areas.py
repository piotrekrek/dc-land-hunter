"""Voivodeship and powiat given as a name or a TERYT code, so Claude need not look codes up first.

Names are compared without case, Polish diacritics or a leading "powiat" / "województwo", so "Dolnoslaskie",
"powiat wrocławski" and "wrocławski" all work. An exact name wins over a longer one that starts with it, so
"Wrocław" is the city and "wrocławski" the county. Anything that stays ambiguous is an error that lists the
candidates: guessing which of two powiats the user meant would show them the wrong land.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.orm import Session

Level = Literal["voivodeship", "powiat", "gmina"]

_CODE_DIGITS: dict[Level, int] = {"voivodeship": 2, "powiat": 4, "gmina": 7}
_LABEL: dict[Level, str] = {"voivodeship": "województwa", "powiat": "powiatu", "gmina": "gminy"}
_MANY: dict[Level, str] = {"voivodeship": "województw", "powiat": "powiatów", "gmina": "gmin"}
_LETTERS = str.maketrans({"ł": "l", "Ł": "l"})  # NFKD does not split the stroke off an l
_PREFIX = re.compile(r"^(powiat|wojewodztwo|woj|gmina)\.?\s+")
MIN_PREFIX = 3  # a shorter start ("a", "wr") would match half of the country
MAX_SHOWN = 8


def normalize(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.strip().casefold().translate(_LETTERS))
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return _PREFIX.sub("", " ".join(plain.split()))


def resolve_area(
    session: Session, level: Level, value: str | None, *, within: str | None = None
) -> str | None:
    """The TERYT code for `value` (a code or a name), or None when `value` is None.

    `within` is a voivodeship code that narrows powiat names ("średzki" exists in two voivodeships).
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    digits = _CODE_DIGITS[level]
    if value.isdigit():
        if len(value) != digits:
            raise ToolError(f"Kod TERYT {_LABEL[level]} ma {digits} cyfry, a „{value}” ma {len(value)}.")
        return value
    found = find_units(session, level, value, within=within)
    if len(found) == 1:
        return str(found[0]["teryt"])
    if not found:
        raise ToolError(
            f"Nie znaleziono {_LABEL[level]} „{value}”. Podaj nazwę po polsku albo kod TERYT; "
            "list_powiaty pokazuje dostępne nazwy."
        )
    raise ToolError(
        f"„{value}” pasuje do kilku {_MANY[level]}: {describe_units(session, level, found)}. Podaj kod TERYT."
    )


def find_units(
    session: Session, level: Level, value: str, *, within: str | None = None, prefix: bool = True
) -> list[dict[str, Any]]:
    """Units whose name equals `value` (ignoring case and diacritics); with `prefix`, when none does, those
    whose name starts with it. A name can match several units: the caller decides what that means."""
    rows = _units(session, level, within)
    key = normalize(value)
    found = [r for r in rows if normalize(r["name"]) == key]
    if not found and prefix and len(key) >= MIN_PREFIX:
        found = [r for r in rows if normalize(r["name"]).startswith(key)]
    return found


def _units(session: Session, level: Level, within: str | None) -> list[dict[str, Any]]:
    sql = "SELECT teryt, name FROM admin_units WHERE level = :level"
    params: dict[str, Any] = {"level": level}
    if within is not None and level == "powiat":
        sql += " AND left(teryt, 2) = :within"
        params["within"] = within
    return [dict(r) for r in session.execute(text(sql + " ORDER BY teryt"), params).mappings()]


def describe_units(session: Session, level: Level, found: list[dict[str, Any]]) -> str:
    """Up to MAX_SHOWN candidates as "Name (TERYT, place)"; a gmina is placed by powiat and voivodeship."""
    powiaty = (
        {
            r["teryt"]: r["name"]
            for r in session.execute(
                text("SELECT teryt, name FROM admin_units WHERE level = 'powiat'")
            ).mappings()
        }
        if level == "gmina"
        else {}
    )
    voivodeships = {
        r["teryt"]: r["name"]
        for r in session.execute(
            text("SELECT teryt, name FROM admin_units WHERE level = 'voivodeship'")
        ).mappings()
    }
    shown = []
    for r in found[:MAX_SHOWN]:
        where = f", {voivodeships.get(r['teryt'][:2], '?')}" if level != "voivodeship" else ""
        if level == "gmina":
            where = f", {powiaty.get(r['teryt'][:4], '?')}{where}"
        shown.append(f"{r['name']} ({r['teryt']}{where})")
    if len(found) > MAX_SHOWN:
        shown.append(f"… i {len(found) - MAX_SHOWN} kolejnych")
    return "; ".join(shown)
