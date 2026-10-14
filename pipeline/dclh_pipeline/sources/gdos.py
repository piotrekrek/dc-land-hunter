"""GDOŚ Geoserwis: protected nature areas (Natura 2000, national parks, nature reserves) via the WFS.

Feeds the hard filter `protected_nature_pct` (brief 2.2 and the deal killers in section 3). `fetch()` pages
through each WFS layer and bundles the pages into one zip snapshot, like the PRG connector. `parse()` reads
them with GDAL's GML driver and returns one GeoDataFrame in EPSG:2180.

Layer names and attributes come from the live GetCapabilities and a sample response (14.10.2026): `nazwa`
(name), `kodinspire` (INSPIRE code). Feature counts that day: 145 OSO, 871 SOO, 46 national parks, 2,152
reserves. `fetch()` checks each layer's total against `numberMatched`, so a server cap cannot produce a
silently truncated snapshot. `parse()` fails loudly if a layer is missing or a geometry lies outside Poland.
"""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import pandas as pd
from pyogrio import read_dataframe

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import POLAND_BOUNDS

WFS_URL = "https://sdi.gdos.gov.pl/wfs"
# kind (used in our tables) -> WFS typeName
LAYERS: dict[str, str] = {
    "natura2000_soo": "GDOS:SpecjalneObszaryOchrony",  # habitats directive (SOO)
    "natura2000_oso": "GDOS:ObszarySpecjalnejOchrony",  # birds directive (OSO)
    "national_park": "GDOS:ParkiNarodowe",
    "reserve": "GDOS:Rezerwaty",
}
NAME_FIELD = "nazwa"
CODE_FIELD = "kodinspire"
EPSG = 2180
PAGE_SIZE = 1000
MAX_PAGES = 50  # safety stop; the largest layer (about 2,150 reserves) needs 3 pages

_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+|unknown)"')
_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_MEMBER = re.compile(rb"<(?:\w+:)?member[\s>]")


class GdosConnector(Source):
    id: ClassVar[str] = "gdos"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for kind, typename in LAYERS.items():
                self._fetch_layer(client, archive, kind, typename)
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WFS_URL, content_type="application/zip"
        )

    def _fetch_layer(self, client: PoliteClient, archive: zipfile.ZipFile, kind: str, typename: str) -> None:
        received = 0
        for page in range(MAX_PAGES):
            params = {
                "service": "WFS",
                "request": "GetFeature",
                "version": "2.0.0",
                "typeNames": typename,
                "srsName": f"EPSG:{EPSG}",
                "count": str(PAGE_SIZE),
                "startIndex": str(received),
            }
            resp = client.get(WFS_URL, params=params)
            resp.raise_for_status()
            content = resp.content
            if b"ExceptionReport" in content[:2000]:
                raise ValueError(f"GDOŚ WFS returned an exception for {typename}")
            returned = _count_returned(content)
            archive.writestr(f"{kind}-{page:04d}.gml", content)
            received += returned
            matched = _number_matched(content)
            if matched is not None:
                if received >= matched:
                    return
                if returned == 0:
                    raise ValueError(f"GDOŚ {kind}: got {received} of {matched} features, then an empty page")
            elif returned < PAGE_SIZE:
                return  # no numberMatched: a short page is the last one
        raise ValueError(f"GDOŚ {kind}: more than {MAX_PAGES} pages; refusing to keep paging")

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        frames: list[gpd.GeoDataFrame] = []
        with zipfile.ZipFile(snapshot.path) as archive:
            names = archive.namelist()
            for kind in LAYERS:
                pattern = re.compile(rf"{kind}-\d+\.gml")
                members = sorted(n for n in names if pattern.fullmatch(n))
                if not members:
                    raise ValueError(f"GDOŚ snapshot has no data for layer {kind!r}")
                frames.extend(_read_layer(kind, archive.read(n)) for n in members)
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        _check_bounds(table)
        return table


def _number_matched(gml: bytes) -> int | None:
    m = _NUMBER_MATCHED.search(gml[:4000])
    return int(m.group(1)) if m and m.group(1) != b"unknown" else None


def _count_returned(gml: bytes) -> int:
    m = _NUMBER_RETURNED.search(gml[:4000])
    return int(m.group(1)) if m else len(_MEMBER.findall(gml))


def _read_layer(kind: str, gml: bytes) -> gpd.GeoDataFrame:
    # GDAL's GML driver needs a file on disk, so the member is written to a temporary directory.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"{kind}.gml"
        path.write_bytes(gml)
        raw = read_dataframe(path)
    # The service writes lower-case names; match them without regard to case, as the PRG connector does.
    by_lower = {c.lower(): c for c in raw.columns}
    name_col, code_col = by_lower.get(NAME_FIELD), by_lower.get(CODE_FIELD)
    if name_col is None or code_col is None:
        missing = [f for f, c in ((NAME_FIELD, name_col), (CODE_FIELD, code_col)) if c is None]
        raise ValueError(f"GDOŚ {kind}: fields {missing} not found; available: {sorted(raw.columns)}")
    keep = raw.geometry.notna() & ~raw.geometry.is_empty
    return gpd.GeoDataFrame(
        {
            "kind": kind,
            "name": raw.loc[keep, name_col].astype(str).to_numpy(),
            "code": raw.loc[keep, code_col].astype(str).to_numpy(),
        },
        geometry=raw.geometry[keep].to_numpy(),
        crs=f"EPSG:{EPSG}",
    )


def _check_bounds(table: gpd.GeoDataFrame) -> None:
    minx, miny, maxx, maxy = table.total_bounds
    x0, y0, x1, y1 = POLAND_BOUNDS
    if minx < x0 or miny < y0 or maxx > x1 or maxy > y1:
        raise ValueError(
            f"GDOŚ coordinates outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); "
            "check the axis order (EPSG:2180)"
        )
