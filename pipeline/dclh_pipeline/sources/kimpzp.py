"""KIMPZP (Krajowa Integracja MPZP): MPZP designations at sampled points of candidate sites (GetFeatureInfo).

Verified 19.10.2026 (docs/SOURCES.md): GetFeatureInfo works only with **version 1.1.1, srs EPSG:4326 and a
lon/lat bbox** (1.3.0 answers "brak serwisu" everywhere). The service forwards the query to the gmina's own
server, so the answer format depends on the gmina. Seen so far:

- Warsaw: XML, `ROWSET name="MPZP_PRZEZNACZENIE_TERENU"` with `ROW` elements; the designation is `FUN_SYMB`
  (e.g. `3k U-O/US`).
- "<gmina>: brak wyniku dla wskazanego obszaru" (no vector plan at the point) and "brak serwisu…".
- GeoServer HTML (`table.featureInfo`), empty when there is no plan at the point.

The parser reads those, plus ESRI-style `FIELDS` attributes, and calls everything else "unparsed". Every raw
answer is kept in the snapshot, so a new layout can be added and re-parsed later without asking again.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from html.parser import HTMLParser
from typing import ClassVar
from xml.etree import ElementTree

import httpx
import pandas as pd
from pyproj import Transformer
from shapely import box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import ZoningLayer
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.model.zoning import sampled_mpzp_kind
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

log = logging.getLogger(__name__)

WMS_URL = (
    "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
)
LAYER = "plany"
HALF_SIDE_DEG = 0.0001  # about 10 m: the answer covers the designations right at the point
PIXELS = 101
POINTS_PER_SITE = 3
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_TO_WGS84 = Transformer.from_crs(2180, 4326, always_xy=True)
# Written in place of an answer when the request failed (no server answers with this).
_FETCH_ERROR = b"dclh-fetch-error: "

# Answer statuses. Only "plan" carries designation codes.
PLAN, NO_PLAN, NO_SERVICE, UNPARSED, ERROR = "plan", "no_plan", "no_service", "unparsed", "error"


def getfeatureinfo_params(lon: float, lat: float) -> dict[str, str | int]:
    d = HALF_SIDE_DEG
    return {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": LAYER,
        "QUERY_LAYERS": LAYER,
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "WIDTH": PIXELS,
        "HEIGHT": PIXELS,
        "X": PIXELS // 2,
        "Y": PIXELS // 2,
        "INFO_FORMAT": "text/html",
    }


def sample_points(geom: BaseGeometry, n: int = POINTS_PER_SITE) -> list[BaseGeometry]:
    """Up to `n` points inside the site (EPSG:2180): one in the middle, then one in each half of it.

    A site is a cluster of parcels and can span two designations, so one point is not enough. The halves are
    cut across the longer side of its bounding box.
    """
    points = [geom.representative_point()]
    x0, y0, x1, y1 = geom.bounds
    if x1 - x0 >= y1 - y0:
        xm = (x0 + x1) / 2
        halves = [box(x0, y0, xm, y1), box(xm, y0, x1, y1)]
    else:
        ym = (y0 + y1) / 2
        halves = [box(x0, y0, x1, ym), box(x0, ym, x1, y1)]
    for half in halves:
        part = geom.intersection(half)
        if not part.is_empty and part.area > 0:
            points.append(part.representative_point())
    return points[:n]


# --- parsing ------------------------------------------------------------------------------------------------


def _symbol_field(fields: Iterable[str]) -> str | None:
    """The column that holds the designation code: a name with SYMB (Warsaw: FUN_SYMB), else OZNACZ."""
    names = list(fields)
    for needle in ("SYMB", "OZNACZ"):
        for name in names:
            if needle in name.upper():
                return name
    return None


def _codes(rows: list[dict[str, str]]) -> list[str] | None:
    """The designation codes of the rows, or None when the rows have no readable designation column."""
    field = _symbol_field({k for row in rows for k in row})
    if field is None:
        return None
    codes = [row.get(field, "").strip() for row in rows]
    codes = [c for c in codes if c and c.lower() != "null"]
    return list(dict.fromkeys(codes)) or None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_rows(text: str) -> list[dict[str, str]] | None:
    try:
        root = ElementTree.fromstring(text.encode())
    except ElementTree.ParseError:
        return None
    rows: list[dict[str, str]] = []
    for el in root.iter():
        name = _local(el.tag).upper()
        if name == "ROW":  # Warsaw
            rows.append({_local(c.tag): (c.text or "") for c in el})
        elif name == "FIELDS" and el.attrib:  # ESRI
            rows.append(dict(el.attrib))
    return rows


class _TableParser(HTMLParser):
    """Rows of `<table>`s as header → cell dicts (GeoServer `featureInfo` and similar)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables = 0
        self.rows: list[dict[str, str]] = []
        self._headers: list[str] = []
        self._cells: list[str] = []
        self._cell: list[str] | None = None
        self._header_row = False
        self._skip = 0  # inside <head>, <style> or <script>
        self.text: list[str] = []  # visible text outside tables

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("head", "style", "script"):
            self._skip += 1
        elif tag == "table":
            self.tables += 1
            self._headers = []
        elif tag == "tr":
            self._cells, self._header_row = [], False
        elif tag in ("td", "th"):
            self._cell = []
            self._header_row = self._header_row or tag == "th"

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif not self._skip and data.strip():
            self.text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("head", "style", "script"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None:
            self._cells.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr":
            if self._header_row:
                self._headers = self._cells
            elif self._headers and self._cells:
                self.rows.append(dict(zip(self._headers, self._cells, strict=False)))


def parse_answer(body: bytes) -> tuple[str, list[str]]:
    """(status, designation codes) of one GetFeatureInfo answer. Codes are raw, e.g. "3k U-O/US"."""
    text = body.decode("utf-8", errors="replace").strip()
    low = text.lower()
    if "brak wyniku" in low:
        return NO_PLAN, []
    if "brak serwisu" in low:
        return NO_SERVICE, []
    if text.startswith("<?xml") or text.startswith("<GetFeatureInfo") or "<row" in low[:2000]:
        rows = _xml_rows(text)
        if rows is not None:
            if not rows:
                return NO_PLAN, []
            codes = _codes(rows)
            return (PLAN, codes) if codes else (UNPARSED, [])
    if "<html" in low or "<table" in low:
        parser = _TableParser()
        parser.feed(text)
        if not parser.rows:
            # GeoServer answers an empty body when nothing is at the point. Text without a table is something
            # else (a raster plan, a message): keep it as unparsed rather than call it "no plan".
            return (UNPARSED, []) if parser.text else (NO_PLAN, [])
        codes = _codes(parser.rows)
        return (PLAN, codes) if codes else (UNPARSED, [])
    return UNPARSED, []


# --- connector ----------------------------------------------------------------------------------------------


class KimpzpConnector(Source):
    """Asks KIMPZP at each (key, lon, lat) point. One snapshot holds every raw answer of a run."""

    id: ClassVar[str] = "kimpzp"

    def __init__(self, spec: SourceSpec, *, points: Sequence[tuple[str, float, float]]) -> None:
        super().__init__(spec)
        self.points = [(str(k), float(lon), float(lat)) for k, lon, lat in points]

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layer": LAYER, "points": self.points}).encode())
            for i, (key, lon, lat) in enumerate(self.points):
                try:
                    resp = client.get(WMS_URL, params=getfeatureinfo_params(lon, lat))
                    resp.raise_for_status()
                    body = resp.content
                except httpx.HTTPError as exc:
                    # One gmina's server failing must not stop the search: the point stays without a plan.
                    log.warning("KIMPZP: no answer at %s (%s, %s): %s", key, lon, lat, exc)
                    body = _FETCH_ERROR + str(exc).encode()
                _write(archive, f"answer_{i:05d}.txt", body)
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WMS_URL, content_type="application/zip"
        )

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """One row per point: key, lon, lat, status, codes (a list of raw designation codes)."""
        rows = []
        with zipfile.ZipFile(snapshot.path) as archive:
            points = json.loads(archive.read("request.json"))["points"]
            for i, (key, lon, lat) in enumerate(points):
                body = archive.read(f"answer_{i:05d}.txt")
                status, codes = (ERROR, []) if body.startswith(_FETCH_ERROR) else parse_answer(body)
                rows.append({"key": key, "lon": lon, "lat": lat, "status": status, "codes": codes})
        return pd.DataFrame(rows, columns=["key", "lon", "lat", "status", "codes"])


def site_points(sites: Sequence[Site]) -> list[tuple[str, float, float]]:
    """The KIMPZP query points of every site, as (site_id, lon, lat)."""
    out = []
    for site in sites:
        for point in sample_points(site.geom):
            lon, lat = _TO_WGS84.transform(point.x, point.y)
            out.append((site.site_id, lon, lat))
    return out


def zoning_layer(sites: Sequence[Site], answers: pd.DataFrame, as_of: datetime | None) -> ZoningLayer:
    """Each site with a plan at its sampled points becomes one zone (the site itself) of its sampled kind.

    Point samples, not the plan's own polygons, so the confidence is `estimated`.
    """
    codes_by_site: dict[str, list[list[str]]] = defaultdict(list)
    for key, codes in zip(answers["key"], answers["codes"], strict=True):
        codes_by_site[str(key)].append(list(codes))
    zones = []
    for site in sites:
        kind = sampled_mpzp_kind(codes_by_site.get(site.site_id, []))
        if kind is not None:
            zones.append((kind, site.geom))
    return ZoningLayer("kimpzp", as_of, tuple(zones), Confidence.ESTIMATED)


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)
