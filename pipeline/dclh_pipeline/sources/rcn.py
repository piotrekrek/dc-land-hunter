"""RCN: the national register of real-estate prices and values (transactions), from the GUGiK WFS.

Feeds the land-price comparables on the site card (`comparables.py`); it has no effect on the score.

Verified from this machine on 12.11.2026 (docs/SOURCES.md): `https://mapy.geoportal.gov.pl/wss/service/rcn`,
WFS 2.0.0, open, no key. Feature type `ms:dzialki` is one polygon per parcel of a transaction (a transaction
with three parcels is three features sharing `TRAN_LOKALNY_ID_IIP`). The answer carries
`numberMatched="unknown"`, so the connector pages with `STARTINDEX` until a page comes back short.

Two traps, both checked against the live service:
  * **Axis order.** With `urn:ogc:def:crs:EPSG::2180` the service follows the EPSG axis order: northing first.
    The BBOX must be sent as `miny,minx,maxy,maxx` and every `posList` reads `northing easting`. A bbox sent
    the usual way silently returns another part of Poland (the first live test did). The connector swaps both
    and `parse` rejects points outside Poland.
  * **A bbox is not a powiat.** The answer includes neighbouring powiats; features are kept only when `TERYT`
    equals the requested powiat.

Personal data (brief 9.6): the notary's name (`DOK_TWORCA`), the deed number (`DOK_OZNACZENIE`,
`TRAN_OZNACZENIE_TRANS`) and the seller and buyer types are never read into the snapshot, not even in the
raw file on disk. Only the fields in `KEPT_FIELDS` are taken from each feature, and the polygon is reduced to
its centroid.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.egib import BBox
from dclh_pipeline.sources.prg import POLAND_BOUNDS

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/rcn"
EPSG = 2180
PAGE_SIZE = 1000
MAX_PAGES = 50  # per box; a fuller box (a big city) is split into quadrants
MAX_SPLIT_DEPTH = 6  # boxes down to ~500 m: central Warsaw and Gdańsk hold over 50 pages in a 2 km box
# XML 1.0 forbids these; the live service passes them through from free-text fields.
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "ms": "http://mapserver.gis.umn.edu/mapserver",
}
# The only attributes read from a feature. Never add the notary, deed number or party fields.
KEPT_FIELDS: dict[str, str] = {
    "tx": "TRAN_LOKALNY_ID_IIP",
    "trans_type": "TRAN_RODZAJ_TRANS",
    "nier_rodzaj": "NIER_RODZAJ",
    "share": "NIER_UDZIAL",
    "nier_area_ha": "NIER_POW_GRUNTU",
    "nier_price": "NIER_CENA_BRUTTO",
    "parcel": "DZI_ID_DZIALKI",
    "parcel_area_ha": "DZI_POW_EWID",
    "parcel_price": "DZI_CENA_BRUTTO",
    "land_use": "DZI_SPOSOB_UZYT",
    "mpzp": "DZI_PRZEZN_WMPZP",
}
FORBIDDEN_COLUMNS = frozenset(
    {"DOK_TWORCA", "DOK_OZNACZENIE", "TRAN_OZNACZENIE_TRANS", "TRAN_SPRZEDAJACY", "TRAN_KUPUJACY"}
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class RcnConnector(Source):
    id: ClassVar[str] = "rcn"

    def __init__(self, spec: SourceSpec, *, powiat: str, bbox: BBox, page_size: int = PAGE_SIZE) -> None:
        super().__init__(spec)
        self.powiat = powiat.zfill(4)
        x0, y0, x1, y1 = bbox
        self.bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
        self.page_size = page_size

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        boxes: list[tuple[BBox, int]] = [(self.bbox, 0)]
        while boxes:
            box, depth = boxes.pop()
            batch = self._fetch_box(client, box)
            if batch is None:
                if depth >= MAX_SPLIT_DEPTH:
                    raise ValueError(f"RCN: more than {MAX_PAGES} pages in a box of powiat {self.powiat}")
                boxes.extend((quarter, depth + 1) for quarter in _quadrants(box))
                continue
            for record in batch:
                key = json.dumps(record, sort_keys=True)  # a parcel on a split line is in both boxes
                if key not in seen:
                    seen.add(key)
                    records.append(record)
        body = {"powiat": self.powiat, "bbox": self.bbox, "records": records}
        return store.save(
            self.id,
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode(),
            suffix=".json",
            url=WFS_URL,
            content_type="application/json",
        )

    def _fetch_box(self, client: PoliteClient, box: BBox) -> list[dict[str, Any]] | None:
        """This powiat's records in one box, or None when the box holds more than MAX_PAGES pages."""
        x0, y0, x1, y1 = box
        wfs_bbox = f"{y0},{x0},{y1},{x1},urn:ogc:def:crs:EPSG::{EPSG}"  # northing first, see the module doc
        records: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            resp = client.get(
                WFS_URL,
                params={
                    "SERVICE": "WFS",
                    "VERSION": "2.0.0",
                    "REQUEST": "GetFeature",
                    "TYPENAMES": "ms:dzialki",
                    "COUNT": self.page_size,
                    "STARTINDEX": page * self.page_size,
                    "BBOX": wfs_bbox,
                },
            )
            resp.raise_for_status()
            returned, batch = parse_page(resp.content)
            records.extend(r for r in batch if r["powiat"] == self.powiat)
            if returned < self.page_size:
                return records
        return None

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        body = json.loads(snapshot.path.read_bytes())
        return transactions_frame(body["records"])


def parse_page(xml: bytes) -> tuple[int, list[dict[str, Any]]]:
    """(features returned by the server, kept parcel records) of one GetFeature answer.

    Raises on a WFS exception report: an error page must not read as "no transactions".
    """
    text = _ILLEGAL_XML.sub("", xml.decode("utf-8", errors="replace"))
    root = ET.fromstring(text.encode())
    if root.tag.endswith("ExceptionReport"):
        raise ValueError(f"RCN returned an error: {xml[:300]!r}")
    returned = int(root.get("numberReturned") or 0)
    out: list[dict[str, Any]] = []
    for feature in root.iterfind("wfs:member/ms:dzialki", _NS):
        record = _record(feature)
        if record is not None:
            out.append(record)
    return returned, out


def _quadrants(box: BBox) -> list[BBox]:
    x0, y0, x1, y1 = box
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return [(x0, y0, mx, my), (mx, y0, x1, my), (x0, my, mx, y1), (mx, my, x1, y1)]


def _text(feature: ET.Element, name: str) -> str:
    el = feature.find(f"ms:{name}", _NS)
    return (el.text or "").strip() if el is not None else ""


def _record(feature: ET.Element) -> dict[str, Any] | None:
    date_el = feature.find("ms:DOK_DATA/gml:timePosition", _NS)
    version_el = feature.find("ms:TRAN_WERSJA_ID/gml:timePosition", _NS)
    tx_date = (date_el.text or "").strip() if date_el is not None else ""
    centroid = _centroid(feature)
    tx = _text(feature, KEPT_FIELDS["tx"])
    if not tx or centroid is None or not _DATE.match(tx_date):
        return None
    record: dict[str, Any] = {name: _text(feature, source) for name, source in KEPT_FIELDS.items()}
    record.update(
        powiat=_text(feature, "TERYT").zfill(4),
        tx_date=tx_date[:10],
        version=(version_el.text or "").strip() if version_el is not None else "",
        x=centroid[0],
        y=centroid[1],
    )
    return record


def _centroid(feature: ET.Element) -> tuple[float, float] | None:
    """Centroid in (easting, northing); `posList` is northing-first (module doc)."""
    polygons = []
    for poly in feature.iterfind(".//gml:Polygon", _NS):
        rings = []
        for pos in poly.iterfind(".//gml:posList", _NS):
            values = [float(v) for v in (pos.text or "").split()]
            rings.append([(values[i + 1], values[i]) for i in range(0, len(values) - 1, 2)])
        if rings and len(rings[0]) >= 4:
            polygons.append(Polygon(rings[0], rings[1:]))
    if not polygons:
        return None
    area = sum(p.area for p in polygons)
    if area <= 0:
        point = polygons[0].representative_point()
        return (point.x, point.y)
    cx = sum(p.centroid.x * p.area for p in polygons) / area
    cy = sum(p.centroid.y * p.area for p in polygons) / area
    return (cx, cy)


def _number(value: str) -> float:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return 0.0


def transactions_frame(records: list[dict[str, Any]]) -> gpd.GeoDataFrame:
    """One row per transaction from parcel records.

    A transaction on several parcels comes as several features. A repeated parcel keeps its newest version;
    the point is the area-weighted mean of the parcel centroids. Price and area are the property's
    (`NIER_*`); when the register leaves them empty the parcels' own figures are summed. A transaction whose
    parcels differ in use gets `land_use = "mieszane"`.
    """
    rows: list[dict[str, Any]] = []
    by_tx: dict[str, dict[str, dict[str, Any]]] = {}
    for rec in records:
        parcels = by_tx.setdefault(rec["tx"], {})
        old = parcels.get(rec["parcel"])
        if old is None or rec["version"] >= old["version"]:
            parcels[rec["parcel"]] = rec
    for tx, parcels in by_tx.items():
        group = list(parcels.values())
        first = group[0]
        weights = [max(_number(p["parcel_area_ha"]), 1e-6) for p in group]
        total = sum(weights)
        uses = {p["land_use"] for p in group}
        price = _number(first["nier_price"]) or sum(_number(p["parcel_price"]) for p in group)
        area = _number(first["nier_area_ha"]) or sum(_number(p["parcel_area_ha"]) for p in group)
        rows.append(
            {
                "tx_id": tx,
                "powiat": first["powiat"],
                "tx_date": date.fromisoformat(first["tx_date"]),
                "price_zl": price,
                "area_ha": area,
                "land_use": uses.pop() if len(uses) == 1 else "mieszane",
                "mpzp": ";".join(sorted({p["mpzp"] for p in group if p["mpzp"]})),
                "nier_rodzaj": first["nier_rodzaj"],
                "trans_type": first["trans_type"],
                "share": first["share"],
                "parcels": len(group),
                "x": sum(p["x"] * w for p, w in zip(group, weights, strict=True)) / total,
                "y": sum(p["y"] * w for p, w in zip(group, weights, strict=True)) / total,
            }
        )
    frame = pd.DataFrame(rows, columns=list(TABLE_COLUMNS))
    table = gpd.GeoDataFrame(
        frame.drop(columns=["x", "y"]),
        geometry=gpd.points_from_xy(frame["x"], frame["y"]),
        crs=f"EPSG:{EPSG}",
    )
    _check_bounds(table)
    return table


TABLE_COLUMNS = (
    "tx_id",
    "powiat",
    "tx_date",
    "price_zl",
    "area_ha",
    "land_use",
    "mpzp",
    "nier_rodzaj",
    "trans_type",
    "share",
    "parcels",
    "x",
    "y",
)


def _check_bounds(table: gpd.GeoDataFrame) -> None:
    if table.empty:
        return
    minx, miny, maxx, maxy = table.total_bounds
    x0, y0, x1, y1 = POLAND_BOUNDS
    if minx < x0 or miny < y0 or maxx > x1 or maxy > y1:
        raise ValueError(
            f"RCN coordinates outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); "
            "check the axis order (northing first in the WFS answer)"
        )
