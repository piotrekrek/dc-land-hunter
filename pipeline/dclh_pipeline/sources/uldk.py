"""ULDK point lookup (`GetParcelByXY`): the parcel under a point (brief section 5, Sprawdź działkę).

Response format, confirmed by the CGE Konin live sample: a status line ("0" = ok), then one line with
`teryt|voivodeship|county|commune|geom_wkt`. The geometry is `SRID=2180;POLYGON(...)` in EPSG:2180.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UldkParcel:
    parcel_id: str  # TERYT-style id, the same as EGiB ID_DZIALKI (e.g. 306201_1.0002.1573)
    voivodeship: str
    county: str
    commune: str
    wkt: str | None  # SRID=2180;POLYGON(...), when requested


def parse_point(text: str) -> UldkParcel:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines or lines[0].strip() != "0":
        message = " ".join(lines) if lines else "pusta odpowiedź"
        raise ValueError(f"ULDK zwrócił błąd: {message}")
    if len(lines) < 2:
        raise ValueError("ULDK: brak rekordu działki w odpowiedzi")
    fields = lines[1].split("|")
    if len(fields) < 4:
        raise ValueError(f"ULDK: nieoczekiwany format rekordu: {lines[1]!r}")
    return UldkParcel(
        parcel_id=fields[0],
        voivodeship=fields[1],
        county=fields[2],
        commune=fields[3],
        wkt=fields[4] if len(fields) > 4 else None,
    )
