"""EGiB parcels by WFS, in tiles inside the candidate zones (brief section 5, step 4; sources.yaml `egib`).

The service is the GUGiK aggregate WFS (`UslugaZbiorcza`). Layer `ms:dzialki` was confirmed by `dclh discover`
(WFS 2.0.0, default page size 1000, EPSG:2180). The attribute names used below are *not* yet checked against a
real response: the first live run must confirm them (see docs/SOURCES.md). The connector keeps the raw pages,
so a parser fix never needs a new download.

Snapshot layout: each WFS page is stored as an object (GML). The snapshot itself is a small JSON bundle that
lists the tiles and their pages, so `parse()` can rebuild the table from the stored objects alone.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import geopandas as gpd
import pandas as pd

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore, object_path
from dclh_pipeline.sources.base import Source

LAYER = "ms:dzialki"
SRS = "EPSG:2180"
TILE_M = 1000.0  # tile edge in metres; small enough that one page rarely hits the server's 1000-feature cap
MAX_TILES = 5000  # guard against an accidentally huge candidate area
MAX_PAGES_PER_TILE = 200
# First column present is the parcel id. ID_DZIALKI is confirmed by the CGE Konin live sample.
ID_COLUMNS = ("ID_DZIALKI", "identyfikator", "gml_id")

BBox = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax in EPSG:2180

_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_MATCHED = re.compile(rb'numberMatched="(\d+)"')
_EXCEPTION_TEXT = re.compile(rb"<ows:ExceptionText>(.*?)</ows:ExceptionText>", re.S)
# The server stamps every response with the time it was produced. Left in, identical parcels hash
# differently on each fetch and every page is stored again (about 20% of stored pages on 19.10.2026).
_TIMESTAMP = re.compile(rb'\s+timeStamp="[^"]*"')


def tile_bbox(bbox: BBox, size_m: float = TILE_M) -> list[BBox]:
    """Split a bbox into tiles aligned to multiples of `size_m`, so overlapping inputs share tiles."""
    x0, y0, x1, y1 = bbox
    tiles: list[BBox] = []
    x = math.floor(x0 / size_m) * size_m
    while x < x1:
        y = math.floor(y0 / size_m) * size_m
        while y < y1:
            tiles.append((x, y, x + size_m, y + size_m))
            y += size_m
        x += size_m
    return tiles


def getfeature_params(bbox: BBox, *, start: int, count: int) -> dict[str, str | int]:
    """WFS 2.0 GetFeature query for one page of parcels inside `bbox` (shared with `dclh capture-sample`).

    `bbox` is (easting, northing, easting, northing). The EPSG:2180 URN means northing-first in WFS 2.0, so
    the request sends northing first. Sending easting first searches the wrong area (checked: the CGE Konin
    sample came back as Poddębice parcels).
    """
    x0, y0, x1, y1 = bbox
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER,
        "srsName": SRS,
        "bbox": f"{y0},{x0},{y1},{x1},urn:ogc:def:crs:EPSG::2180",  # northing, easting
        "count": count,
        "startIndex": start,
    }


def _count(pattern: re.Pattern[bytes], body: bytes) -> int | None:
    match = pattern.search(body[:4096])  # the counts sit in the root element
    return int(match.group(1)) if match else None


def _raise_if_exception(body: bytes) -> None:
    """WFS errors come back as HTTP 200 with an ExceptionReport; never treat those as data."""
    if b"ExceptionReport" in body[:4096]:
        text = _EXCEPTION_TEXT.search(body)
        detail = text.group(1).decode("utf-8", "replace") if text else "nieznany błąd"
        raise RuntimeError(f"EGiB WFS zwrócił wyjątek: {detail}")


class EgibConnector(Source):
    id: ClassVar[str] = "egib"

    def __init__(
        self,
        spec: SourceSpec,
        *,
        bboxes: Sequence[BBox],
        page_size: int = 1000,
    ) -> None:
        super().__init__(spec)
        self.bboxes = list(bboxes)
        self.page_size = page_size
        self.url = next(str(p.url) for p in spec.probes if getattr(p, "kind", None) == "ogc")

    def _tiles(self) -> list[BBox]:
        tiles = sorted({t for bbox in self.bboxes for t in tile_bbox(bbox)})
        if len(tiles) > MAX_TILES:
            raise ValueError(f"EGiB: {len(tiles)} kafelków to za dużo (limit {MAX_TILES}); zawęź obszar")
        return tiles

    def _fetch_tile(self, client: PoliteClient, store: SnapshotStore, tile: BBox) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        start = 0
        for _ in range(MAX_PAGES_PER_TILE):
            params = getfeature_params(tile, start=start, count=self.page_size)
            response = client.get(self.url, params=params)
            response.raise_for_status()
            body = _TIMESTAMP.sub(b"", response.content, count=1)  # the parser never reads it; see _TIMESTAMP
            _raise_if_exception(body)
            returned = _count(_RETURNED, body)
            matched = _count(_MATCHED, body)
            sha = store.put_object(self.id, body, suffix=".gml")
            pages.append({"sha256": sha, "suffix": ".gml", "returned": returned or 0})
            if not returned:
                break
            if matched is not None and start + returned >= matched:
                break
            # The live service reports numberMatched="unknown": a short page is the last one.
            if matched is None and returned < self.page_size:
                break
            start += returned
        else:
            raise RuntimeError(f"EGiB: kafelek {tile} ma więcej niż {MAX_PAGES_PER_TILE} stron")
        return pages

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        if not self.bboxes:
            raise ValueError("EGiB: brak obszarów kandydujących (bboxes); pobieranie działek wymaga strefy")
        tiles = [
            {"bbox": list(tile), "pages": self._fetch_tile(client, store, tile)} for tile in self._tiles()
        ]
        bundle = {"layer": LAYER, "srs": SRS, "tiles": tiles}
        return store.save(
            self.id,
            json.dumps(bundle, ensure_ascii=False).encode("utf-8"),
            suffix=".json",
            url=self.url,
            content_type="application/json",
        )

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        bundle = json.loads(snapshot.path.read_text(encoding="utf-8"))
        objects_dir = snapshot.path.parents[1]  # <source>/objects/<xx>/<bundle>.json
        frames: list[gpd.GeoDataFrame] = []
        for tile in bundle["tiles"]:
            for page in tile["pages"]:
                if page["returned"] == 0:
                    continue
                path = object_path(objects_dir, page["sha256"], page["suffix"])
                frames.append(read_page(path))
        if not frames:
            return gpd.GeoDataFrame(
                {"parcel_id": pd.Series(dtype=str)}, geometry=gpd.GeoSeries(dtype="geometry"), crs=SRS
            )
        return _combine(frames)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"parcel_id": pd.Series(dtype=str)}, geometry=gpd.GeoSeries([], dtype="geometry"), crs=SRS
    )


def read_page(path: Path) -> gpd.GeoDataFrame:
    """One stored WFS page as a GeoDataFrame in EPSG:2180, coordinates (easting, northing).

    GDAL applies the EPSG axis order (northing first) when it reads the GML, and gives easting-northing
    geometries. Checked on the CGE Konin sample against ULDK: the parcel 101103_5.0011.1 comes out at
    easting 485065, northing 448637, which is the same place as ULDK's geometry for that id.
    """
    frame = gpd.read_file(path)
    return frame.set_crs(SRS, allow_override=True) if frame.crs is None else frame.to_crs(SRS)


def _combine(frames: Iterable[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """One row per parcel: the same parcel can come back from two neighbouring tiles."""
    parts = []
    for frame in frames:
        id_column = next((c for c in ID_COLUMNS if c in frame.columns), None)
        if id_column is None:
            raise ValueError(f"EGiB: w odpowiedzi nie ma żadnej z kolumn {ID_COLUMNS}; sprawdź schemat WFS")
        parts.append(
            gpd.GeoDataFrame({"parcel_id": frame[id_column].astype(str)}, geometry=frame.geometry, crs=SRS)
        )
    table = pd.concat(parts, ignore_index=True)
    table = table[table.geometry.notna() & ~table.geometry.is_empty]
    table = table.drop_duplicates(subset="parcel_id", keep="first")
    return gpd.GeoDataFrame(table.reset_index(drop=True), geometry="geometry", crs=SRS)
