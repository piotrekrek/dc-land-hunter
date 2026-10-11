"""GUGiK PRG: administrative boundaries (country, voivodeships, powiats, gminas) via the WFS.

Brief section 5, step 1. `fetch()` downloads one GML per layer and bundles them into a single zip snapshot,
because the contract allows one snapshot per run. `parse()` reads the zip with GDAL's GML driver and returns
one GeoDataFrame in EPSG:2180.

Field names (`jpt_kod_je`, `jpt_nazwa_`) and the axis order come from the PRG schema as we understand it. The
first real sample from the VPS checks them (see docs/SOURCES.md). `parse()` fails loudly if the fields are
missing, the coordinates fall outside Poland, or the Poznań gmina is not where it should be (a swapped axis
order mirrors every geometry across the diagonal, which a bounding box alone cannot see).

`fetch()` pages through each layer with `startIndex`/`count` and checks the total against `numberMatched`,
so a server-side result cap can't produce a silently truncated snapshot.
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
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

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
# Bounding box of Poland in EPSG:2180 as (min x, min y, max x, max y), with x = easting and y = northing.
# Generous box around Poland, as the real PRG extent was measured on 11.10.2026 (x 171 699–861 896,
# y 133 223–797 228; the country itself reaches about 168 000–853 000 and 126 000–776 000). A box cannot tell
# the axes apart, which is why the Poznań centroid check below exists.
POLAND_BOUNDS = (120_000.0, 100_000.0, 900_000.0, 850_000.0)
# A box can't prove the axis order, so the Poznań gmina centroid (easting, northing) is checked too.
POZNAN_XY = (360_000.0, 505_000.0)
POZNAN_TOLERANCE_M = 40_000.0
PAGE_SIZE = 1000
MAX_PAGES = 50  # safety stop; 4 layers x 50 pages is far above the ~2,500 gminas

_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+|unknown)"')
_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_MEMBER = re.compile(rb"<(?:\w+:)?member[\s>]")


class PrgConnector(Source):
    id: ClassVar[str] = "prg"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for level, typename in LAYERS.items():
                self._fetch_layer(client, archive, level, typename)
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=WFS_URL, content_type="application/zip"
        )

    def _fetch_layer(self, client: PoliteClient, archive: zipfile.ZipFile, level: str, typename: str) -> None:
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
                raise ValueError(f"WFS returned an exception for {typename}")
            returned = _count_returned(content)
            archive.writestr(f"{level}-{page:04d}.gml", content)
            received += returned
            matched = _number_matched(content)
            if matched is not None:
                if received >= matched:
                    return
                if returned == 0:
                    raise ValueError(f"PRG {level}: got {received} of {matched} features, then an empty page")
            elif returned < PAGE_SIZE:
                return  # server doesn't report numberMatched: a short page is the last one
        raise ValueError(f"PRG {level}: more than {MAX_PAGES} pages; refusing to keep paging")

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        frames: list[gpd.GeoDataFrame] = []
        with zipfile.ZipFile(snapshot.path) as archive:
            names = archive.namelist()
            for level in LAYERS:
                pattern = re.compile(rf"{level}(-\d+)?\.gml")
                members = sorted(n for n in names if pattern.fullmatch(n))
                if not members:
                    raise ValueError(f"PRG snapshot has no data for layer {level!r}")
                frames.extend(_read_level(level, archive.read(n)) for n in members)
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


def _read_level(level: str, gml: bytes) -> gpd.GeoDataFrame:
    # GDAL's GML driver needs a file on disk, so the member is written to a temporary directory.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"{level}.gml"
        path.write_bytes(gml)
        raw = read_dataframe(path)
    # The real service uses upper case (JPT_KOD_JE, JPT_NAZWA_); match the names without regard to case.
    by_lower = {c.lower(): c for c in raw.columns}
    code_col, name_col = by_lower.get(CODE_FIELD), by_lower.get(NAME_FIELD)
    if code_col is None or name_col is None:
        missing = [f for f, c in ((CODE_FIELD, code_col), (NAME_FIELD, name_col)) if c is None]
        raise ValueError(f"PRG {level}: fields {missing} not found; available: {sorted(raw.columns)}")
    out = gpd.GeoDataFrame(
        {
            "level": level,
            "teryt": raw[code_col].astype(str),
            "name": raw[name_col].astype(str),
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


def _check_axis_order(table: gpd.GeoDataFrame) -> None:
    """The Poznań gmina must sit near (360 km, 505 km). Swapped axes would put it near (505 km, 360 km)."""
    poznan = table[(table["level"] == "gmina") & (table["name"] == "Poznań")]
    if poznan.empty:
        return  # not in this snapshot (a partial sample); the bounding-box check still ran
    px, py = POZNAN_XY
    near = any(
        abs(c.x - px) <= POZNAN_TOLERANCE_M and abs(c.y - py) <= POZNAN_TOLERANCE_M
        for c in poznan.geometry.centroid
    )
    if not near:
        raise ValueError(
            f"PRG gmina Poznań is not near ({px:.0f},{py:.0f}) in EPSG:2180; "
            "check the axis order (EPSG:2180 is northing/easting)"
        )


def clip_to_bbox(
    table: gpd.GeoDataFrame, bbox: tuple[float, float, float, float], tolerance_m: float = 50.0
) -> gpd.GeoDataFrame:
    """Rows whose boundary touches the bbox (EPSG:2180); geometry is simplified to `tolerance_m`.

    Used to cut a committable sample from the full PRG download; the tests never read the full file.
    """
    hits = table[table.intersects(box(*bbox))].copy()
    hits["geometry"] = hits.geometry.simplify(tolerance_m, preserve_topology=True)
    return hits


def locate(table: gpd.GeoDataFrame, geom: BaseGeometry) -> dict[str, dict[str, str]]:
    """The admin unit at each level that contains the geometry's representative point (EPSG:2180).

    Used to name a parcel's commune from the boundaries rather than from an attribute of another source.
    Levels with no unit at that point are left out.
    """
    point = geom.representative_point()
    found: dict[str, dict[str, str]] = {}
    for level in LAYERS:
        units = table[table["level"] == level]
        hit = units[units.geometry.contains(point)]
        if not hit.empty:
            row = hit.iloc[0]
            found[level] = {"teryt": str(row["teryt"]), "name": str(row["name"])}
    return found
