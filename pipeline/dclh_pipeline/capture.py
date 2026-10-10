"""Capture a small sample of live responses for one place (`dclh capture-sample`).

Run from a machine in Poland (the VPS), with DCLH_CONTACT_EMAIL set. It writes the raw responses and a
metadata file into one directory. The directory is small (a few parcels), so it can be committed as fixtures.
Nothing here parses data: the goal is to record exactly what the services return.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyproj import Transformer

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.sources.egib import getfeature_params

ULDK_URL = "https://uldk.gugik.gov.pl/"
ULDK_RESULT = "teryt,voivodeship,county,commune,geom_wkt"
SAMPLE_PARCELS = (
    20  # the WFS page size for a sample: small enough to commit, big enough to see the attributes
)

_to_2180 = Transformer.from_crs(4326, 2180, always_xy=True)


@dataclass(frozen=True)
class Place:
    name: str
    lon: float
    lat: float
    radius_m: float

    @property
    def centre(self) -> tuple[float, float]:
        return _to_2180.transform(self.lon, self.lat)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        x, y = self.centre
        return (x - self.radius_m, y - self.radius_m, x + self.radius_m, y + self.radius_m)


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def capture(
    client: PoliteClient,
    *,
    egib_url: str,
    place: Place,
    out_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save one WFS page and one ULDK lookup for `place` into `out_dir`. Returns the metadata written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = (now or datetime.now(UTC)).isoformat()
    records: list[dict[str, Any]] = []

    params = getfeature_params(place.bbox, start=0, count=SAMPLE_PARCELS)
    response = client.get(egib_url, params=params)
    body = response.content
    gml = out_dir / "egib_getfeature.gml"
    gml.write_bytes(body)
    records.append(_record("egib", str(response.request.url), response.status_code, body, gml.name))

    x, y = place.centre
    uldk_params = {"request": "GetParcelByXY", "xy": f"{x:.2f},{y:.2f},2180", "result": ULDK_RESULT}
    uldk = client.get(ULDK_URL, params=uldk_params)
    text = out_dir / "uldk_getparcelbyxy.txt"
    text.write_bytes(uldk.content)
    records.append(_record("uldk", str(uldk.request.url), uldk.status_code, uldk.content, text.name))

    meta = {
        "place": place.name,
        "lon": place.lon,
        "lat": place.lat,
        "radius_m": place.radius_m,
        "bbox_2180": list(place.bbox),
        "fetched_at": fetched_at,
        "responses": records,
    }
    (out_dir / "capture.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def _record(source: str, url: str, status: int, body: bytes, file: str) -> dict[str, Any]:
    return {
        "source_id": source,
        "url": url,
        "status": status,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "file": file,
    }


def default_out_dir(root: Path, place: Place) -> Path:
    return root / "tests" / "fixtures" / "live" / _slug(place.name)
