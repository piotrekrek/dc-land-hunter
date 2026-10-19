"""ISOK flood hazard maps: the 1% (Q100) zones for one search area, from the PGW Wody Polskie WFS.

Feeds the hard filter `flood_10_or_1_pct` (constraint layer `flood_10_or_1`). Verified 19.10.2026
(docs/SOURCES.md): the flat layer `wms_nz-core:NZ.Fluvial` has `loo_ql_probabilityofoccurrence` 0.1 / 0.01 /
0.002. The connector asks only for 0.01, which also returns the levee-breach scenario at Q1%. It does not ask
for the 10% zones: a 10% flood is modelled inside the 1% extent, so the 1% zones already cover them. The 0.2%
zones (a penalty, not a filter) are not fetched yet.

Zones are whole river reaches (13 zones near Warsaw are 22 MB), so the connector fetches one bbox (a powiat),
pages with startIndex and checks the total against numberMatched. The answer to
`srsName=urn:ogc:def:crs:EPSG::2180` is northing-first; `parse()` swaps the axes and then checks that every
zone touches the requested bbox, so a wrong axis order fails loudly instead of putting the river in the wrong
place. Zones are clipped to the bbox: the search only needs that area, and a whole reach is slow to overlay on
every site.
"""

from __future__ import annotations

import io
import json
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

WFS_URL = "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_HY_10k_2019_WFS"
LAYER = "wms_nz-core:NZ.Fluvial"
PROBABILITY_FIELD = "loo_ql_probabilityofoccurrence"
LIKELIHOOD_FIELD = "loo_qualitativelikelihood"
VALID_TO_FIELD = "validityperiod_end"
PROBABILITY = 0.01
EPSG = 2180
PAGE_SIZE = 20  # a zone near Warsaw is about 2 MB, so pages stay small
MAX_PAGES = 100
# A fixed member date, so identical pages make an identical zip and the store keeps one object.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+|unknown)"')
_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_TIMESTAMP = re.compile(rb'\s+timeStamp="[^"]*"')


def getfeature_params(bbox: BBox, *, start: int, count: int) -> dict[str, str | int]:
    """One page of 1% zones inside `bbox` (easting, northing). The FES envelope is sent northing-first."""
    x0, y0, x1, y1 = bbox
    fes = (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
        "<fes:And>"
        f"<fes:PropertyIsEqualTo><fes:ValueReference>{PROBABILITY_FIELD}</fes:ValueReference>"
        f"<fes:Literal>{PROBABILITY}</fes:Literal></fes:PropertyIsEqualTo>"
        "<fes:BBOX><fes:ValueReference>geom</fes:ValueReference>"
        '<gml:Envelope srsName="urn:ogc:def:crs:EPSG::2180">'
        f"<gml:lowerCorner>{y0} {x0}</gml:lowerCorner><gml:upperCorner>{y1} {x1}</gml:upperCorner>"
        "</gml:Envelope></fes:BBOX>"
        "</fes:And></fes:Filter>"
    )
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": LAYER,
        "srsName": "urn:ogc:def:crs:EPSG::2180",
        "filter": fes,
        "count": count,
        "startIndex": start,
    }


class IsokConnector(Source):
    id: ClassVar[str] = "isok"

    def __init__(self, spec: SourceSpec, *, bbox: BBox, page_size: int = PAGE_SIZE) -> None:
        super().__init__(spec)
        x0, y0, x1, y1 = bbox
        self.bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
        self.page_size = page_size

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layer": LAYER, "bbox": self.bbox}).encode())
            start, matched = 0, None
            for page in range(MAX_PAGES):
                resp = client.get(
                    WFS_URL, params=getfeature_params(self.bbox, start=start, count=self.page_size)
                )
                resp.raise_for_status()
                body = _TIMESTAMP.sub(b"", resp.content, count=1)
                if b"ExceptionReport" in body[:2000]:
                    raise ValueError(f"ISOK WFS returned an exception: {body[:500]!r}")
                m = _NUMBER_MATCHED.search(body[:4000])
                if m is None or m.group(1) == b"unknown":
                    raise ValueError("ISOK: the response has no numberMatched; cannot check it is complete")
                matched = int(m.group(1))
                r = _NUMBER_RETURNED.search(body[:4000])
                returned = int(r.group(1)) if r else 0
                if returned:
                    _write(archive, f"page_{page:03d}.gml", body)
                start += returned
                if returned == 0 or start >= matched:
                    break
            else:
                raise ValueError(f"ISOK: more than {MAX_PAGES} pages for bbox {self.bbox}")
            if start < matched:
                raise ValueError(f"ISOK: got {start} of {matched} zones; the response is incomplete")
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WFS_URL, content_type="application/zip"
        )

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        with zipfile.ZipFile(snapshot.path) as archive:
            request = json.loads(archive.read("request.json"))
            pages = sorted(n for n in archive.namelist() if n.startswith("page_"))
            frames = [_read_page(archive.read(name)) for name in pages]
        area = box(*request["bbox"])
        if not frames:
            return _empty()
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        table = table[table[PROBABILITY_FIELD] == PROBABILITY]
        # The server selected the zones by the bbox, so they must touch it. A server may match on envelopes,
        # so one zone outside is possible; none inside means swapped axes or a wrong CRS. Fail rather than
        # load a river in the wrong place.
        if not table.empty and not table.geometry.intersects(area).any():
            raise ValueError(
                f"ISOK: none of the {len(table)} zones touches the requested bbox; "
                "check the axis order (EPSG:2180, the service answers northing-first)"
            )
        clipped = table.geometry.clip_by_rect(*request["bbox"])
        keep = ~clipped.is_empty
        return gpd.GeoDataFrame(
            {
                "probability": table.loc[keep, PROBABILITY_FIELD].to_numpy(),
                "scenario": table.loc[keep, LIKELIHOOD_FIELD].astype(str).to_numpy(),
                "valid_to": table.loc[keep, VALID_TO_FIELD].astype(str).to_numpy(),
            },
            geometry=clipped[keep].to_numpy(),
            crs=f"EPSG:{EPSG}",
        )


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)


def _read_page(gml: bytes) -> gpd.GeoDataFrame:
    # GDAL's GML driver needs a file on disk. SWAP_COORDINATES=YES because the service writes northing first;
    # stating it explicitly keeps the result independent of how GDAL reads the srsName form.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "page.gml"
        path.write_bytes(gml)
        raw = read_dataframe(path, SWAP_COORDINATES="YES")
    missing = [f for f in (PROBABILITY_FIELD, LIKELIHOOD_FIELD, VALID_TO_FIELD) if f not in raw.columns]
    if missing:
        raise ValueError(f"ISOK: fields {missing} not found; available: {sorted(raw.columns)}")
    raw[PROBABILITY_FIELD] = pd.to_numeric(raw[PROBABILITY_FIELD], errors="coerce")
    raw = raw[raw.geometry.notna() & ~raw.geometry.is_empty]
    return gpd.GeoDataFrame(raw, geometry="geometry").set_crs(f"EPSG:{EPSG}", allow_override=True)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "probability": pd.Series(dtype=float),
            "scenario": pd.Series(dtype=str),
            "valid_to": pd.Series(dtype=str),
        },
        geometry=gpd.GeoSeries([], dtype="geometry"),
        crs=f"EPSG:{EPSG}",
    )
