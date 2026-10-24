"""ULDK point lookup (`GetParcelByXY`): the parcel under a point (brief section 5, Sprawdź działkę).

Response format, confirmed by the CGE Konin live sample: a status line ("0" = ok), then one line with
`teryt|voivodeship|county|commune|geom_wkt`. The geometry is `SRID=2180;POLYGON(...)` in EPSG:2180.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyproj import Transformer

from dclh_pipeline.http import PoliteClient

ULDK_URL = "https://uldk.gugik.gov.pl/"
RESULT_FIELDS = "teryt,voivodeship,county,commune,geom_wkt"
_TO_PUWG = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


@dataclass(frozen=True)
class UldkParcel:
    parcel_id: str  # TERYT-style id, the same as EGiB ID_DZIALKI (e.g. 306201_1.0002.1573)
    voivodeship: str
    county: str
    commune: str
    wkt: str | None  # SRID=2180;POLYGON(...), when requested


def parse_records(text: str) -> list[UldkParcel]:
    """Every parcel record of a ULDK answer (a name search can match several parcels)."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines or lines[0].strip() != "0":
        message = " ".join(lines) if lines else "pusta odpowiedź"
        raise ValueError(f"ULDK zwrócił błąd: {message}")
    if len(lines) < 2:
        raise ValueError("ULDK: brak rekordu działki w odpowiedzi")
    parcels = []
    for line in lines[1:]:
        fields = line.split("|")
        if len(fields) < 4:
            raise ValueError(f"ULDK: nieoczekiwany format rekordu: {line!r}")
        parcels.append(
            UldkParcel(
                parcel_id=fields[0],
                voivodeship=fields[1],
                county=fields[2],
                commune=fields[3],
                wkt=fields[4] if len(fields) > 4 else None,
            )
        )
    return parcels


def parse_point(text: str) -> UldkParcel:
    return parse_records(text)[0]


def wgs84_to_puwg(lat: float, lon: float) -> tuple[float, float]:
    """(easting, northing) in EPSG:2180: the order ULDK wants in `xy` (confirmed by the CGE Konin sample)."""
    x, y = _TO_PUWG.transform(lon, lat)
    return float(x), float(y)


def by_xy_params(x: float, y: float) -> dict[str, str]:
    return {"request": "GetParcelByXY", "xy": f"{x:.2f},{y:.2f},2180", "result": RESULT_FIELDS}


def by_id_params(parcel_id: str) -> dict[str, str]:
    return {"request": "GetParcelById", "id": parcel_id, "result": RESULT_FIELDS, "srid": "2180"}


def by_name_params(text: str) -> dict[str, str]:
    """Search by "obręb numer" text. UNVERIFIED: `GetParcelByIdOrNr` is not confirmed live yet."""
    return {"request": "GetParcelByIdOrNr", "id": text, "result": RESULT_FIELDS, "srid": "2180"}


def lookup(client: PoliteClient, params: dict[str, Any]) -> list[UldkParcel]:
    """Ask ULDK and parse the answer. A "no result" answer raises ValueError with ULDK's own text."""
    response = client.get(ULDK_URL, params=params)
    response.raise_for_status()
    return parse_records(response.text)
