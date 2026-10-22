"""Industrial and brownfield areas from the stored OSM extract: the input of the powered-brownfield signal.

Brief section 3: a closed or underused industrial site with its own 110 kV substation is gold.
`powered_brownfield()` looks for an industrial area near the site with such a substation inside it, and
`zoning_fact()` uses the same areas as a fallback when no plan covers the site. Both need polygons, so this
reads `landuse=industrial` and `landuse=brownfield` from the `osm_power` snapshot (the Geofabrik Poland
extract, the same file the grid comes from), confidence `estimated`:

- closed ways and multipolygon relations are assembled into areas by libosmium (`FileProcessor.with_areas`),
  since large plants are often mapped as relations;
- the extract is cut first with osmium-tool (cached next to the snapshot, like the power and infra cuts); the
  cut keeps the member ways and nodes, so relations still assemble. The area pass needs a location index over
  every node of the file it reads: small for the cut, gigabytes for the full extract. So without osmium-tool
  the full extract is not read at all and the layer stays missing (`MAX_UNCUT_MB`).
- `bounds` (EPSG:2180) keeps only the areas whose outline reaches into that box, so a powiat search holds a
  few hundred polygons, not the whole country.

OSM `landuse=industrial` covers working and closed plants alike, so the layer is "industrial or
brownfield", not "brownfield". That is what the signal needs: the substation decides.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import osmium
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import box

from dclh_pipeline.model.features import GeomIndex, Layer
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.sources.osm_power import _power_source
from dclh_pipeline.sources.prg import EPSG

log = logging.getLogger(__name__)

LANDUSE = ("industrial", "brownfield")
INDUSTRIAL_FILTER = tuple(f"wr/landuse={value}" for value in LANDUSE)
SOURCE_ID = "osm_power"  # the same snapshot the grid comes from
# An uncut file larger than this is not read: its node location index would not fit in memory on the VPS.
MAX_UNCUT_MB = 100

BBox = tuple[float, float, float, float]


def read_industrial(snapshot_path: Path, bounds: BBox | None = None) -> gpd.GeoDataFrame | None:
    """Industrial and brownfield areas in EPSG:2180: columns `landuse`, `osm_ref` (`w123` / `r456`), `name`.

    `bounds` (EPSG:2180) keeps only the areas that reach into the box. None when only the full extract is
    available and it is too large to read safely (see the module docstring).
    """
    path = _power_source(snapshot_path, INDUSTRIAL_FILTER, "industrial")
    if path == snapshot_path and path.stat().st_size > MAX_UNCUT_MB * 1024 * 1024:
        log.warning(
            "OSM industrial: osmium-tool is needed to cut %s first; industrial layer skipped", path.name
        )
        return None
    window = None
    if bounds is not None:
        to_wgs84 = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
        window = box(*to_wgs84.transform_bounds(*bounds))
    started = time.monotonic()
    wkb = osmium.geom.WKBFactory()
    rows: list[dict[str, str]] = []
    geoms: list[shapely.Geometry] = []
    landuse = osmium.filter.TagFilter(*(("landuse", value) for value in LANDUSE))
    for obj in (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.AREA)
        .with_areas(landuse)
        .with_filter(landuse)
    ):
        if not isinstance(obj, osmium.osm.Area):
            continue
        try:
            geom = shapely.from_wkb(wkb.create_multipolygon(obj))
        except RuntimeError:  # an invalid ring that libosmium could not assemble
            continue
        if window is not None and not geom.intersects(window):
            continue
        prefix = "w" if obj.from_way() else "r"
        rows.append(
            {
                "landuse": obj.tags.get("landuse") or "",
                "osm_ref": f"{prefix}{obj.orig_id()}",
                "name": obj.tags.get("name") or "",
            }
        )
        geoms.append(geom)
    table = gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["landuse", "osm_ref", "name"]), geometry=geoms, crs="EPSG:4326"
    ).to_crs(f"EPSG:{EPSG}")
    log.info(
        "OSM industrial: %d industrial and %d brownfield areas (%d from relations) in %.0f s",
        int((table["landuse"] == "industrial").sum()),
        int((table["landuse"] == "brownfield").sum()),
        int(table["osm_ref"].str.startswith("r").sum()),
        time.monotonic() - started,
    )
    return table


def industrial_layer(table: gpd.GeoDataFrame | None, as_of: datetime | None) -> Layer | None:
    """The `industrial` layer of `SiteLayers`; None (powered brownfield missing) when nothing was read."""
    if table is None:
        return None
    return Layer(
        source_id=SOURCE_ID, index=GeomIndex(table.geometry), as_of=as_of, confidence=Confidence.ESTIMATED
    )
