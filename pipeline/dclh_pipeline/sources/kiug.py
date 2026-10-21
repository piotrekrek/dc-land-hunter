"""KIUG (Krajowa Integracja Użytków Gruntowych): soil classes of the parcels at sampled points of a site.

Feeds only the penalty `soil_class_iii_pct`, as an estimate (owner decision 21.10.2026). The I–II hard filter
is not fed: an estimated share must never exclude a site.

Verified 21.10.2026 from the VPS: the service is WMS only (no WFS). GetFeatureInfo works with version
1.1.1, `SRS=EPSG:2180` (easting first) and `INFO_FORMAT=application/vnd.ogc.gml` on layer `klasouzytki`. The
answer is about the whole **parcel** under the point, not the contour: `Identyfikator działki`, `Pole pow.
w ewidencji gruntów (ha)` and `Oznaczenie konturu`, the list of the parcel's land-use contours with their
class (e.g. `RIIIa,RIIIb`, `Lzr/PsV,RV`, `B`). It names the classes, not their areas, so a parcel's class
III share is estimated by splitting the parcel evenly over the contours it lists. Where no parcel is
published the answer says "Usługa nie udostępnia danych opisowych dla wybranego obiektu".

A contour counts as class III when its arable (R), grassland (Ps) or meadow (Ł) part has class III, IIIa or
IIIb, also behind a prefix (`S-RIIIa`, `Lzr/PsIII`). No other class symbol contains "III". Forest soil classes
(`LsIII`) do not count: forest has its own filter.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import zipfile
from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar
from xml.etree import ElementTree

import httpx
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.types import Fact
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

log = logging.getLogger(__name__)

WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzytkowGruntowych"
LAYER = "klasouzytki"
HALF_SIDE_M = 50  # a 101 × 101 px window of 100 m: one pixel is about 1 m
PIXELS = 101
HA_PER_POINT = 2.0  # about one sample point per 2 ha of site
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
# Written in place of an answer when the request failed (no server answers with this).
_FETCH_ERROR = b"dclh-fetch-error: "

PARCEL_FIELD = "Identyfikator działki"
AREA_FIELD = "Pole pow. w ewidencji gruntów (ha)"
CONTOUR_FIELD = "Oznaczenie konturu"
# Answer statuses. Only "parcel" carries a parcel and its contours.
PARCEL, NO_PARCEL, UNPARSED, ERROR = "parcel", "no_parcel", "unparsed", "error"

_CLASS_III = re.compile(r"(?:R|Ps|Ł)III")


def getfeatureinfo_params(x: float, y: float) -> dict[str, str | int]:
    d = HALF_SIDE_M
    return {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": LAYER,
        "QUERY_LAYERS": LAYER,
        "STYLES": "",
        "SRS": "EPSG:2180",
        "BBOX": f"{x - d},{y - d},{x + d},{y + d}",
        "WIDTH": PIXELS,
        "HEIGHT": PIXELS,
        "X": PIXELS // 2,
        "Y": PIXELS // 2,
        "INFO_FORMAT": "application/vnd.ogc.gml",
        "FEATURE_COUNT": 1,
    }


def soil_points(geom: BaseGeometry, max_points: int) -> list[Point]:
    """About one point per `HA_PER_POINT` inside the site (EPSG:2180), at most `max_points`, at least one.

    Points sit at the cell centres of a grid fitted to the site's bounding box, so they spread over its
    parcels instead of bunching up. The grid gets finer until enough centres fall inside the site (an
    irregular site leaves many outside), then an even selection of them is kept.
    """
    wanted = max(1, min(max_points, round(geom.area / 10_000 / HA_PER_POINT)))
    x0, y0, x1, y1 = geom.bounds
    step = math.sqrt(geom.area / wanted)
    inside: list[Point] = []
    for _ in range(6):
        nx, ny = max(1, round((x1 - x0) / step)), max(1, round((y1 - y0) / step))
        grid = (
            Point(x0 + (i + 0.5) * (x1 - x0) / nx, y0 + (j + 0.5) * (y1 - y0) / ny)
            for j in range(ny)
            for i in range(nx)
        )
        inside = [p for p in grid if geom.contains(p)]
        if len(inside) >= wanted:
            break
        step /= 1.5
    if len(inside) > wanted:
        inside = [inside[k * len(inside) // wanted] for k in range(wanted)]
    return inside or [geom.representative_point()]


def site_soil_points(sites: Sequence[Site], max_points: int) -> list[tuple[str, float, float]]:
    """The KIUG query points of every site, as (site_id, x, y) in EPSG:2180."""
    return [(s.site_id, p.x, p.y) for s in sites for p in soil_points(s.geom, max_points)]


# --- parsing ------------------------------------------------------------------------------------------------


def parse_answer(body: bytes) -> tuple[str, dict[str, str]]:
    """(status, attributes) of one GetFeatureInfo answer. Attributes are the `Attribute` elements by name."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return UNPARSED, {}
    attributes = {
        el.attrib["Name"]: (el.text or "").strip()
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "Attribute" and "Name" in el.attrib
    }
    if attributes.get(PARCEL_FIELD):
        return PARCEL, attributes
    if not attributes or "Informacja" in attributes:
        return NO_PARCEL, {}
    return UNPARSED, {}


def class_iii_share(contours: str) -> float:
    """Share of a parcel that is class III, splitting it evenly over its listed contours (0 when none)."""
    parts = [c.strip() for c in contours.split(",") if c.strip()]
    if not parts:
        return 0.0
    return sum(1 for c in parts if _CLASS_III.search(c)) / len(parts)


def _hectares(text: str) -> float | None:
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


# --- connector ----------------------------------------------------------------------------------------------


class KiugConnector(Source):
    """Asks KIUG at each (key, x, y) point (EPSG:2180). One snapshot holds every raw answer of a run."""

    id: ClassVar[str] = "kiug"

    def __init__(self, spec: SourceSpec, *, points: Sequence[tuple[str, float, float]]) -> None:
        super().__init__(spec)
        self.points = [(str(k), float(x), float(y)) for k, x, y in points]

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layer": LAYER, "points": self.points}).encode())
            for i, (key, x, y) in enumerate(self.points):
                try:
                    resp = client.get(WMS_URL, params=getfeatureinfo_params(x, y))
                    resp.raise_for_status()
                    body = resp.content
                except httpx.HTTPError as exc:
                    # One failed point must not stop the search: the site is estimated from its other points.
                    log.warning("KIUG: no answer at %s (%s, %s): %s", key, x, y, exc)
                    body = _FETCH_ERROR + str(exc).encode()
                _write(archive, f"answer_{i:05d}.xml", body)
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WMS_URL, content_type="application/zip"
        )

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """One row per point: key, x, y, status, parcel_id, parcel_ha, contours."""
        rows = []
        with zipfile.ZipFile(snapshot.path) as archive:
            points = json.loads(archive.read("request.json"))["points"]
            for i, (key, x, y) in enumerate(points):
                body = archive.read(f"answer_{i:05d}.xml")
                status, attrs = (ERROR, {}) if body.startswith(_FETCH_ERROR) else parse_answer(body)
                rows.append(
                    {
                        "key": key,
                        "x": x,
                        "y": y,
                        "status": status,
                        "parcel_id": attrs.get(PARCEL_FIELD),
                        "parcel_ha": _hectares(attrs.get(AREA_FIELD, "")),
                        "contours": attrs.get(CONTOUR_FIELD, ""),
                    }
                )
        return pd.DataFrame(rows, columns=["key", "x", "y", "status", "parcel_id", "parcel_ha", "contours"])


def soil_iii_facts(answers: pd.DataFrame, as_of: datetime | None) -> dict[str, Fact[float]]:
    """Estimated class III share (percent) per site key, from the distinct parcels its points hit.

    Each parcel counts once, weighted by its registered area. A site whose points hit no parcel with an area
    gets no fact, so it stays "missing".
    """
    parcels = answers[(answers["status"] == PARCEL) & answers["parcel_ha"].notna()]
    parcels = parcels.drop_duplicates(subset=["key", "parcel_id"])
    out: dict[str, Fact[float]] = {}
    for key, group in parcels.groupby("key"):
        total = float(group["parcel_ha"].sum())
        iii = float((group["parcel_ha"] * group["contours"].map(class_iii_share)).sum())
        out[str(key)] = Fact(round(100 * iii / total, 1), "kiug", as_of, Confidence.ESTIMATED)
    return out


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)
