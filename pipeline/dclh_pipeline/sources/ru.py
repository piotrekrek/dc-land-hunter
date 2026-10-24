"""Rejestr Urbanistyczny (RU): plan ogólny zones and MPZP outlines for one search area, from the APP WFS.

Verified 19.10.2026 (docs/SOURCES.md, "Hard filters and zoning"):

- `app-pog/wfs`, layer `app-pog:StrefaPlanistyczna`: plan ogólny zones. `symbol` (SJ, SU, SP…) and the zone
  type in `nazwa` (an xlink to the code list). The strefa gospodarcza is `nazwa = …/strefaGospodarcza`,
  symbol SP.
  A server-side filter on `nazwa` returns nothing, so the client filters (`model/zoning.py`).
- `app-mpzp/wfs`, layer `app-mpzp:AktPlanowaniaPrzestrzennego`: MPZP outlines only, no designations. Its
  `status` tells a plan in force (`legalForce`) from one in procedure (`elaboration`, `adoption`).

Both answer EPSG:2180 northing-first and return at most 10 features per request whatever `count` asks (the
server's CountDefault), so the connector pages with startIndex over one bbox (a powiat) and checks the total
against numberMatched. The xlink attributes (`status`, `nazwa`) are read with GDAL's
GML_ATTRIBUTES_TO_OGR_FIELDS, as `<field>_href`.
"""

from __future__ import annotations

import io
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import pandas as pd
from pyogrio import read_dataframe
from shapely import box

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.egib import BBox

log = logging.getLogger(__name__)

BASE_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe"
EPSG = 2180
SRS = "urn:ogc:def:crs:EPSG::2180"
PAGE_SIZE = 10  # the server's CountDefault; it never returns more per request
MAX_PAGES = 2000
MAX_SKIPPED = 20  # broken features skipped per layer and bbox before the fetch fails instead
# A fixed member date, so identical pages make an identical zip and the store keeps one object.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+|unknown)"')
_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_TIMESTAMP = re.compile(rb'\s+timeStamp="[^"]*"')


def _counts(body: bytes) -> tuple[int, int]:
    """(numberMatched, numberReturned) of one answer."""
    m = _NUMBER_MATCHED.search(body[:4000])
    if m is None or m.group(1) == b"unknown":
        raise ValueError("RU: the response has no numberMatched; cannot check it is complete")
    r = _NUMBER_RETURNED.search(body[:4000])
    return int(m.group(1)), int(r.group(1)) if r else 0


def getfeature_params(layer: str, bbox: BBox, *, start: int, count: int) -> dict[str, str | int]:
    """One page of `layer` inside `bbox` (easting, northing). The bbox is sent northing-first."""
    x0, y0, x1, y1 = bbox
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "srsName": SRS,
        "bbox": f"{y0},{x0},{y1},{x1},{SRS}",
        "count": count,
        "startIndex": start,
    }


class _RuWfs(Source):
    """Pages one RU layer over a bbox. Subclasses name the layer and the columns they keep."""

    id: ClassVar[str] = "ru"
    path: ClassVar[str]
    layer: ClassVar[str]
    columns: ClassVar[dict[str, str]]  # output column → GDAL field

    def __init__(self, spec: SourceSpec, *, bbox: BBox, page_size: int = PAGE_SIZE) -> None:
        super().__init__(spec)
        x0, y0, x1, y1 = bbox
        self.bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
        self.page_size = page_size

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.path}"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layer": self.layer, "bbox": self.bbox}).encode())
            start, got, matched, written = 0, 0, None, 0
            skipped: list[int] = []

            def keep(body: bytes) -> int:
                nonlocal matched, got, written
                matched, returned = _counts(body)
                if returned:
                    _write(archive, f"page_{written:05d}.gml", body)
                    written += 1
                    got += returned
                return returned

            for _ in range(MAX_PAGES):
                body = self._page(client, start, self.page_size)
                if body is not None:
                    returned = keep(body)
                    start += returned
                    if returned == 0 or start >= (matched or 0):
                        break
                    continue
                # The server refuses a whole page when one feature on it is broken (19.10.2026: one MPZP
                # outline in the 1421 bbox). Ask that page one feature at a time; skip only the broken ones.
                for i in range(start, start + self.page_size):
                    one = self._page(client, i, 1)
                    if one is None:
                        skipped.append(i)
                    else:
                        keep(one)
                if matched is None or len(skipped) > MAX_SKIPPED:
                    raise ValueError(
                        f"RU: the server refused {len(skipped)} features of {self.layer} for bbox {self.bbox}"
                    )
                start += self.page_size
                if start >= matched:
                    break
            else:
                raise ValueError(f"RU: more than {MAX_PAGES} pages of {self.layer} for bbox {self.bbox}")
            if matched is None or got + len(skipped) < matched:
                raise ValueError(
                    f"RU: got {got} of {matched} features of {self.layer}; the response is incomplete"
                )
            if skipped:
                log.warning("RU: skipped %d broken features of %s at %s", len(skipped), self.layer, skipped)
                _write(archive, "skipped.json", json.dumps(skipped).encode())
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=self.url, content_type="application/zip"
        )

    def _page(self, client: PoliteClient, start: int, count: int) -> bytes | None:
        """One answer without its timestamp, or None when the server refuses it (400 "Invalid request")."""
        resp = client.get(self.url, params=getfeature_params(self.layer, self.bbox, start=start, count=count))
        if resp.status_code == 400:
            return None
        resp.raise_for_status()
        body = _TIMESTAMP.sub(b"", resp.content, count=1)
        if b"ExceptionReport" in body[:2000]:
            raise ValueError(f"RU WFS returned an exception: {body[:500]!r}")
        return body

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        with zipfile.ZipFile(snapshot.path) as archive:
            request = json.loads(archive.read("request.json"))
            if request["layer"] != self.layer:
                raise ValueError(f"RU: snapshot holds {request['layer']}, not {self.layer}")
            pages = sorted(n for n in archive.namelist() if n.startswith("page_"))
            frames = [self._read_page(archive.read(name)) for name in pages]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return self._empty()
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        # The server matches on envelopes, so a feature can surround a small box (a parcel check asks for a
        # few hundred metres) without touching it: a real "no plan here". Swapped axes put every feature
        # far outside the box, so no extent reaches it: fail rather than put plans in the wrong place.
        if not table.geometry.envelope.intersects(box(*request["bbox"])).any():
            raise ValueError(
                f"RU: none of the {len(table)} features reaches the requested bbox; "
                "check the axis order (EPSG:2180, the service answers northing-first)"
            )
        # Plans are drawn by gminas; an invalid ring would break the overlay with the sites.
        invalid = ~table.geometry.is_valid
        table.loc[invalid, "geometry"] = table.geometry[invalid].make_valid()
        return table.drop_duplicates("gml_id").drop(columns="gml_id").reset_index(drop=True)

    def _read_page(self, gml: bytes) -> gpd.GeoDataFrame:
        # GDAL's GML driver needs a file on disk (and writes a .gfs next to it, so use a fresh directory).
        # SWAP_COORDINATES=YES because the service writes northing first.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "page.gml"
            path.write_bytes(gml)
            raw = read_dataframe(path, SWAP_COORDINATES="YES", GML_ATTRIBUTES_TO_OGR_FIELDS="YES")
        missing = [f for f in ("gml_id", *self.columns.values()) if f not in raw.columns]
        if missing:
            raise ValueError(f"RU {self.layer}: fields {missing} not found; available: {sorted(raw.columns)}")
        raw = raw[raw.geometry.notna() & ~raw.geometry.is_empty]
        out = gpd.GeoDataFrame(
            {"gml_id": raw["gml_id"].astype(str), **{c: raw[f].astype(str) for c, f in self.columns.items()}},
            geometry=raw.geometry,
        )
        return out.set_crs(f"EPSG:{EPSG}", allow_override=True)

    def _empty(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {c: pd.Series(dtype=str) for c in self.columns},
            geometry=gpd.GeoSeries([], dtype="geometry"),
            crs=f"EPSG:{EPSG}",
        )


class RuPlanOgolnyConnector(_RuWfs):
    """Plan ogólny zones: one row per zone with its plan, symbol, zone type and status (code-list hrefs)."""

    path = "app-pog/wfs"
    layer = "app-pog:StrefaPlanistyczna"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "symbol": "symbol",
        "zone_href": "nazwa_href",
        "status_href": "status_href",
    }


class RuMpzpConnector(_RuWfs):
    """MPZP outlines: one row per plan with its status (code-list href). No designations."""

    path = "app-mpzp/wfs"
    layer = "app-mpzp:AktPlanowaniaPrzestrzennego"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "status_href": "status_href",
    }


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)
