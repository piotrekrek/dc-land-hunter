"""Wind and solar clusters from the stored OSM extract: the input of the `bonus_res` score item.

The scorer gives `bonus.res_cluster_pts` when an OZE cluster lies within `bonus.res_cluster_km` of the site
(`config/scoring.yaml`). It does not say what a cluster is, so this module does (22.10.2026):

- An OZE object is `power=generator` with `generator:source` wind or solar, or `power=plant` with
  `plant:source` wind or solar, mapped as a node or a closed way. Relations are skipped (the generators of a
  plant relation are mapped on their own). Other renewables (biogas, hydro) are small in Poland and left out.
- Its capacity is `generator:output:electricity` / `plant:output:electricity` when tagged with a number
  ("2 MW", "750 kWp"). Otherwise a wind turbine counts `WIND_TURBINE_MW` and a solar outline counts its area
  times `SOLAR_MW_PER_HA`. An untagged solar node (usually a rooftop) counts nothing.
- Objects under `MIN_OBJECT_MW` (rooftop PV) are dropped, then objects within `LINK_M` of each other are
  grouped (single linkage). A group is a cluster when it has at least `MIN_CLUSTER_MW`. A plant outline and
  the generators inside it describe the same capacity, so a group counts the larger of the two sums.

The distance is from the site to the nearest member of a cluster. OSM maps only part of the installed
capacity, so the fact is `estimated` and "no cluster" means "none in OSM".
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
from shapely import unary_union
from shapely.geometry import Point, Polygon

from dclh_pipeline.model.features import GeomIndex, Layer
from dclh_pipeline.model.grid import WAY_ID_OFFSET
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import _components
from dclh_pipeline.sources.osm_power import _NodeLocations, _power_source
from dclh_pipeline.sources.prg import EPSG

log = logging.getLogger(__name__)

RES_FILTER = ("nw/power=generator,plant",)
SOURCE_ID = "osm_power"  # the same snapshot the grid comes from
WIND, SOLAR = "wind", "solar"

WIND_TURBINE_MW = 2.0  # a typical onshore turbine in Poland when the output is not tagged
SOLAR_MW_PER_HA = 0.8  # ground-mounted PV: about 1.25 ha per MWp
MIN_OBJECT_MW = 0.1  # below this an object is rooftop PV, not part of a cluster
LINK_M = 2_000.0  # objects this close belong to the same cluster
MIN_CLUSTER_MW = 20.0

_OUTPUT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([kmg]?)wp?\s*$", re.IGNORECASE)
_UNIT_MW = {"K": 0.001, "": 0.000001, "M": 1.0, "G": 1000.0}


def parse_output_mw(value: str | None) -> float | None:
    """MW from an `*:output:electricity` tag ("2 MW", "2,3 MW", "750 kWp"); else None ("yes")."""
    match = _OUTPUT.match(value or "")
    if match is None:
        return None
    return float(match.group(1).replace(",", ".")) * _UNIT_MW[match.group(2).upper()]


def _classify(tags: osmium.osm.TagList) -> tuple[str, str, float | None] | None:
    """(power, source, tagged MW) of a wind or solar generator or plant, else None."""
    power = tags.get("power")
    if power not in ("generator", "plant"):
        return None
    sources = set((tags.get(f"{power}:source") or "").split(";"))
    source = WIND if WIND in sources else SOLAR if SOLAR in sources else None
    if source is None:
        return None
    return power, source, parse_output_mw(tags.get(f"{power}:output:electricity"))


def read_res(snapshot_path: Path) -> gpd.GeoDataFrame:
    """Wind and solar objects in EPSG:2180: columns `power`, `source`, `osm_id`, `mw` (estimated if untagged).

    Nodes are points and closed ways polygons; open ways (rare) are skipped.
    """
    path = str(_power_source(snapshot_path, RES_FILTER, "res"))
    started = time.monotonic()
    rows: list[dict[str, object]] = []
    geoms: list[object] = []
    way_rows: list[dict[str, object]] = []
    refs: list[np.ndarray] = []
    for obj in osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY).with_filter(
        osmium.filter.KeyFilter("power")
    ):
        kind = _classify(obj.tags)
        if kind is None:
            continue
        power, source, mw = kind
        if isinstance(obj, osmium.osm.Node) and obj.location.valid():
            rows.append({"power": power, "source": source, "osm_id": obj.id, "tagged_mw": mw})
            geoms.append(Point(obj.location.lon, obj.location.lat))
        elif isinstance(obj, osmium.osm.Way):
            nodes = np.fromiter((n.ref for n in obj.nodes), dtype=np.int64)
            if len(nodes) >= 4 and nodes[0] == nodes[-1]:
                way_rows.append(
                    {"power": power, "source": source, "osm_id": obj.id + WAY_ID_OFFSET, "tagged_mw": mw}
                )
                refs.append(nodes)
    locations = _NodeLocations.read(path, np.concatenate(refs) if refs else None)
    for row, r in zip(way_rows, refs, strict=True):
        outline = locations.way_geometry(r, "line")
        if outline is not None:  # None: a member node is cut off at the border
            rows.append(row)
            geoms.append(Polygon(outline.coords))
    table = gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["power", "source", "osm_id", "tagged_mw"]),
        geometry=geoms,
        crs="EPSG:4326",
    ).to_crs(f"EPSG:{EPSG}")
    table["mw"] = _estimated_mw(table)
    log.info(
        "OSM OZE: %d wind and %d solar objects (%d with a tagged output) in %.0f s",
        int((table["source"] == WIND).sum()),
        int((table["source"] == SOLAR).sum()),
        int(table["tagged_mw"].notna().sum()),
        time.monotonic() - started,
    )
    return table.drop(columns="tagged_mw")


def _estimated_mw(table: gpd.GeoDataFrame) -> np.ndarray:
    area_ha = np.where(table.geom_type == "Polygon", table.area / 10_000, 0.0)
    untagged = np.where(
        (table["source"] == WIND) & (table["power"] == "generator"),
        WIND_TURBINE_MW,
        np.where(table["source"] == SOLAR, area_ha * SOLAR_MW_PER_HA, 0.0),
    )
    return np.where(table["tagged_mw"].notna(), table["tagged_mw"].fillna(0.0), untagged)


def res_clusters(
    objects: gpd.GeoDataFrame,
    link_m: float = LINK_M,
    min_object_mw: float = MIN_OBJECT_MW,
    min_cluster_mw: float = MIN_CLUSTER_MW,
) -> gpd.GeoDataFrame:
    """Clusters of at least `min_cluster_mw`: columns `objects`, `wind`, `solar`, `mw` and the members."""
    kept = objects[objects["mw"] >= min_object_mw].reset_index(drop=True)
    rows = []
    for members in _components(list(kept.geometry), link_m):
        group = kept.iloc[members]
        by_power = group.groupby("power")["mw"].sum()
        rows.append(
            {
                "objects": len(group),
                "wind": int((group["source"] == WIND).sum()),
                "solar": int((group["source"] == SOLAR).sum()),
                "mw": float(max(by_power.get("generator", 0.0), by_power.get("plant", 0.0))),
                "geometry": unary_union(group.geometry.values),
            }
        )
    clusters = gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["objects", "wind", "solar", "mw", "geometry"]),
        geometry="geometry",
        crs=objects.crs,
    )
    return clusters[clusters["mw"] >= min_cluster_mw].reset_index(drop=True)


def res_cluster_layer(objects: gpd.GeoDataFrame, as_of: datetime | None) -> Layer:
    """The OZE clusters of the extract, for `dist_res_cluster_km`."""
    clusters = res_clusters(objects)
    log.info("OSM OZE: %d clusters of at least %.0f MW", len(clusters), MIN_CLUSTER_MW)
    return Layer(
        source_id=SOURCE_ID,
        index=GeomIndex(clusters.geometry),
        as_of=as_of,
        confidence=Confidence.ESTIMATED,
    )
