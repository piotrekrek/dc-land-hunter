"""Turn what a person pastes into "Sprawdź działkę" into lookups ULDK can answer (PLAN.md).

Pure text handling, no network. Each non-empty line (or `;`-separated part) becomes one lookup, or one issue
with a Polish message that says what to fix. A line holding several parcel ids gives one lookup per id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote

MAX_INPUTS = 20

Kind = Literal["id", "point", "name"]

# 6 or 7 digit TERYT of the gmina, type digit, obręb, parcel number (may carry an "AR_n." sheet part).
_ID = re.compile(r"\b\d{6,7}_\d\.\d{4}\.[\w./]*\w")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_MAPS_HOST = re.compile(r"(?:google\.[a-z.]+/maps|maps\.app\.goo\.gl|goo\.gl/maps)", re.I)
_MAPS_AT = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
_MAPS_BANG = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_MAPS_Q = re.compile(r"[?&](?:q|query|ll)=(-?\d+\.\d+)(?:,|%2C)\s*(-?\d+\.\d+)")
_NAME = re.compile(r"^(?P<place>(?:[^\W\d_]|\d{4})[\w .'-]*?)\s+(?P<number>\d+[A-Za-z]?(?:/\d+)?)$")
_BARE_NUMBER = re.compile(r"^\d+[A-Za-z]?(?:/\d+)?$")


@dataclass(frozen=True)
class ParcelInput:
    kind: Kind
    text: str  # what to show back: the id, "lat N, lon E", or the "obręb numer" text
    parcel_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    query: str | None = None


@dataclass(frozen=True)
class InputIssue:
    text: str
    message_pl: str


@dataclass
class ParsedInputs:
    items: list[ParcelInput] = field(default_factory=list)
    issues: list[InputIssue] = field(default_factory=list)
    dropped: int = 0  # valid items beyond MAX_INPUTS
    duplicates: int = 0


def _wgs_point(a: float, b: float) -> tuple[float, float] | None:
    """(lat, lon) when the pair lies in Poland, in either order. The two ranges do not overlap."""
    for lat, lon in ((a, b), (b, a)):
        if 48.5 <= lat <= 55.5 and 13.5 <= lon <= 24.5:
            return lat, lon
    return None


def _from_numbers(line: str) -> ParcelInput | InputIssue | None:
    """Coordinates: two numbers and only separators, degree signs and N/E besides. None for any other line."""
    numbers = _NUMBER.findall(line)
    if len(numbers) != 2 or re.sub(r"[\s,;°NEne]", "", _NUMBER.sub("", line)):
        return None
    a, b = (float(n.replace(",", ".")) for n in numbers)
    point = _wgs_point(a, b)
    if point is None:
        return InputIssue(
            line,
            "Te współrzędne leżą poza Polską. Podaj szerokość i długość geograficzną, "
            "na przykład 52,2297 20,9511.",
        )
    lat, lon = point
    return ParcelInput("point", f"{lat:.5f} N, {lon:.5f} E", lat=lat, lon=lon)


def _from_maps_link(line: str) -> ParcelInput | InputIssue:
    text = unquote(line)
    for pattern in (_MAPS_BANG, _MAPS_AT, _MAPS_Q):
        match = pattern.search(text)
        if match:
            point = _wgs_point(float(match.group(1)), float(match.group(2)))
            if point:
                lat, lon = point
                return ParcelInput("point", f"{lat:.5f} N, {lon:.5f} E", lat=lat, lon=lon)
    return InputIssue(
        line,
        "Ten link z Map Google nie zawiera współrzędnych (skrócone linki ich nie mają). "
        "Otwórz go i skopiuj adres z paska przeglądarki albo wklej współrzędne.",
    )


def parse_inputs(raw: str, limit: int = MAX_INPUTS) -> ParsedInputs:
    out = ParsedInputs()
    seen: set[str] = set()

    def add(item: ParcelInput) -> None:
        key = item.parcel_id or item.query or item.text
        if key in seen:
            out.duplicates += 1
        elif len(out.items) >= limit:
            out.dropped += 1
        else:
            seen.add(key)
            out.items.append(item)

    for part in re.split(r"[\r\n;]+", raw):
        line = part.strip()
        if not line:
            continue
        ids = [m.group(0) for m in _ID.finditer(line)]
        if ids:
            for parcel_id in ids:
                add(ParcelInput("id", parcel_id, parcel_id=parcel_id))
            continue
        if _MAPS_HOST.search(line):
            result: ParcelInput | InputIssue | None = _from_maps_link(line)
        else:
            result = _from_numbers(line)
        if result is None:
            name = _NAME.match(line)
            if name:
                query = f"{name.group('place').strip()} {name.group('number')}"
                result = ParcelInput("name", query, query=query)
            elif _BARE_NUMBER.match(line):
                result = InputIssue(
                    line,
                    f"Brakuje obrębu. Dopisz obręb (na przykład „Wola {line}”) "
                    "albo wklej pełny identyfikator.",
                )
            else:
                result = InputIssue(
                    line,
                    "Nie rozpoznaję tego zapisu. Użyj identyfikatora działki, współrzędnych, "
                    "linku z Map Google albo zapisu „obręb numer”.",
                )
        if isinstance(result, InputIssue):
            out.issues.append(result)
        else:
            add(result)
    return out
