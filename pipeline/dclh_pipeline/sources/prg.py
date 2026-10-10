"""GUGiK PRG: administrative boundaries (country, voivodeships, powiats, gminas) via the WFS.

Brief section 5, step 1. `fetch()` downloads one GML per layer and bundles them into a single zip snapshot,
because the contract allows one snapshot per run. `parse()` reads the zip with GDAL's GML driver and returns
one GeoDataFrame in EPSG:2180.

Field names (`jpt_kod_je`, `jpt_nazwa_`) and the axis order come from the PRG schema as we understand it. The
first real sample from the VPS checks them (see docs/SOURCES.md). `parse()` fails loudly if the fields are
missing or the coordinates fall outside Poland, which is what a swapped axis order would produce.
"""

from __future__ import annotations

import io
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

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"

# level name in our tables -> WFS layer name (from the discovery capabilities, 2026-10-10)
LAYERS: dict[str, str] = {
    "country": "ms:A00_Granice_panstwa",
    "voivodeship": "ms:A01_Granice_wojewodztw",
    "powiat": "ms:A02_Granice_powiatow",
    "gmina": "ms:A03_Granice_gmin",
}
CODE_FIELD = "jpt_kod_je"  # TERYT code
NAME_FIELD = "jpt_nazwa_"
EPSG = 2180
# Rough bounding box of Poland in EPSG:2180 (metres). Coordinates outside it mean the axis order is wrong.
POLAND_BOUNDS = (100_000.0, 100_000.0, 900_000.0, 900_000.0)


class PrgConnector(Source):
    id: ClassVar[str] = "prg"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for level, typename in LAYERS.items():
                params = {
                    "service": "WFS",
                    "request": "GetFeature",
                    "version": "2.0.0",
                    "typeNames": typename,
                    "srsName": f"EPSG:{EPSG}",
                }
                resp = client.get(WFS_URL, params=params)
                resp.raise_for_status()
                if b"ExceptionReport" in resp.content[:2000]:
                    raise ValueError(f"WFS returned an exception for {typename}")
                archive.writestr(f"{level}.gml", resp.content)
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WFS_URL, content_type="application/zip"
        )

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        frames = []
        with zipfile.ZipFile(snapshot.path) as archive:
            for level in LAYERS:
                frames.append(_read_level(level, archive.read(f"{level}.gml")))
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        _check_bounds(table)
        return table


def _read_level(level: str, gml: bytes) -> gpd.GeoDataFrame:
    # GDAL's GML driver needs a file on disk, so the member is written to a temporary directory.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"{level}.gml"
        path.write_bytes(gml)
        raw = read_dataframe(path)
    missing = {CODE_FIELD, NAME_FIELD} - set(raw.columns)
    if missing:
        raise ValueError(f"PRG {level}: fields {sorted(missing)} not found; available: {sorted(raw.columns)}")
    out = gpd.GeoDataFrame(
        {
            "level": level,
            "teryt": raw[CODE_FIELD].astype(str),
            "name": raw[NAME_FIELD].astype(str),
        },
        geometry=raw.geometry.values,
        crs=f"EPSG:{EPSG}",
    )
    return out


def _check_bounds(table: gpd.GeoDataFrame) -> None:
    minx, miny, maxx, maxy = table.total_bounds
    x0, y0, x1, y1 = POLAND_BOUNDS
    if minx < x0 or miny < y0 or maxx > x1 or maxy > y1:
        raise ValueError(
            f"PRG coordinates outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); "
            "check the axis order (EPSG:2180 is northing/easting)"
        )
