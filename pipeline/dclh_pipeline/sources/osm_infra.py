"""Gas pipelines and runways from the stored OSM extract: proxies for two penalties (owner, 21.10.2026).

No public source exists for either (checked 21.10.2026). Gaz-System publishes no WMS or WFS of its
transmission network, and the obstacle limitation surfaces (OLS) of airports are held by ULC and each airport
operator. So both come from the `osm_power` snapshot (the Geofabrik Poland extract), confidence `estimated`:

- `gas_pipeline_zone`: the site lies within `proxies.gas_zone_m` of a way tagged `man_made=pipeline` with
  `substance=gas` (or the older `type=gas`). The extract of 12.10.2026 has 877 such ways, 8,774 km, against
  about 11,000 km of Gaz-System transmission pipelines; the much larger distribution network is mostly not
  mapped. So "no pipeline" means "none in OSM", never "safe".
- `airport_ols`: the site lies within the inner horizontal surface of a runway (`aeroway=runway`), a radius
  set by the runway length after ICAO Annex 14, table 4-1: code 1 (under 800 m) 2,000 m, code 2 (under
  1,200 m) 2,500 m, codes 3–4 4,000 m. Runways shorter than `proxies.runway_min_m` (farm strips: 86 of 413 in
  the extract) are skipped. A runway mapped as an area uses half its outline as its length.

`read_infra()` cuts the extract with osmium-tool (cached next to the snapshot, like the power cut) and reads
node locations in batches (`_NodeLocations`), so memory stays small. Without the cut, one pass over the 2 GB
file took 4 minutes on the VPS.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd

from dclh_pipeline.model.features import GeomIndex, Layer
from dclh_pipeline.model.grid import WAY_ID_OFFSET
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.sources.osm_power import _NodeLocations, _power_source
from dclh_pipeline.sources.prg import EPSG

log = logging.getLogger(__name__)

INFRA_FILTER = ("w/man_made=pipeline", "w/aeroway=runway")
GAS_PIPELINE = "gas_pipeline"
RUNWAY = "runway"
SOURCE_ID = "osm_power"  # the same snapshot the grid comes from

# ICAO Annex 14, table 4-1: inner horizontal surface radius by aerodrome code (here: by runway length).
_OLS_RADII_M: tuple[tuple[float, float], ...] = ((800.0, 2000.0), (1200.0, 2500.0))
_OLS_RADIUS_LONG_M = 4000.0


def _kind(tags: osmium.osm.TagList) -> str | None:
    if tags.get("man_made") == "pipeline" and "gas" in (tags.get("substance"), tags.get("type")):
        return GAS_PIPELINE
    if tags.get("aeroway") == RUNWAY:
        return RUNWAY
    return None


def read_infra(snapshot_path: Path) -> gpd.GeoDataFrame:
    """Gas pipelines and runways in EPSG:2180: columns `kind`, `osm_id`, `name`, `length_m`."""
    path = str(_power_source(snapshot_path, INFRA_FILTER, "infra"))
    started = time.monotonic()
    rows: list[dict[str, object]] = []
    refs: list[np.ndarray] = []
    for way in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(
        osmium.filter.KeyFilter("man_made", "aeroway")
    ):
        if not isinstance(way, osmium.osm.Way):
            continue
        kind = _kind(way.tags)
        nodes = np.fromiter((n.ref for n in way.nodes), dtype=np.int64)
        if kind is None or len(nodes) < 2:
            continue
        name = way.tags.get("name") or way.tags.get("ref") or ""
        rows.append({"kind": kind, "osm_id": way.id + WAY_ID_OFFSET, "name": name})
        refs.append(nodes)
    locations = _NodeLocations.read(path, np.concatenate(refs) if refs else None)
    keep = [
        (row, locations.way_geometry(r, "line"), bool(r[0] == r[-1]))
        for row, r in zip(rows, refs, strict=True)
    ]
    keep = [k for k in keep if k[1] is not None]  # None: a member node is cut off at the border
    table = gpd.GeoDataFrame(
        pd.DataFrame([row for row, _, _ in keep], columns=["kind", "osm_id", "name"]),
        geometry=[geom for _, geom, _ in keep],
        crs="EPSG:4326",
    ).to_crs(f"EPSG:{EPSG}")
    closed = np.array([is_closed for _, _, is_closed in keep], dtype=bool)
    table["length_m"] = np.where(closed, table.length / 2, table.length)
    log.info(
        "OSM infra: %d gas pipelines, %d runways in %.0f s",
        int((table["kind"] == GAS_PIPELINE).sum()),
        int((table["kind"] == RUNWAY).sum()),
        time.monotonic() - started,
    )
    return table


def ols_radius_m(runway_length_m: float) -> float:
    """Inner horizontal surface radius for a runway of this length (ICAO Annex 14, table 4-1)."""
    for below, radius in _OLS_RADII_M:
        if runway_length_m < below:
            return radius
    return _OLS_RADIUS_LONG_M


def gas_zone_layer(infra: gpd.GeoDataFrame, zone_m: float, as_of: datetime | None) -> Layer:
    """The strip within `zone_m` of each gas pipeline in OSM."""
    gas = infra[infra["kind"] == GAS_PIPELINE]
    return Layer(
        source_id=SOURCE_ID,
        index=GeomIndex(gas.geometry.buffer(zone_m)),
        as_of=as_of,
        confidence=Confidence.ESTIMATED,
    )


def airport_ols_layer(infra: gpd.GeoDataFrame, runway_min_m: float, as_of: datetime | None) -> Layer:
    """The inner horizontal surface around each runway of at least `runway_min_m`."""
    runways = infra[(infra["kind"] == RUNWAY) & (infra["length_m"] >= runway_min_m)]
    zones = [
        geom.buffer(ols_radius_m(length))
        for geom, length in zip(runways.geometry, runways["length_m"], strict=True)
    ]
    return Layer(source_id=SOURCE_ID, index=GeomIndex(zones), as_of=as_of, confidence=Confidence.ESTIMATED)
