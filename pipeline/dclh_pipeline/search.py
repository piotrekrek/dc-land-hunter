"""One search run for an area: the funnel from grid buffers to scored sites (PLAN.md, Phase 1.5).

Stages, each counted in the report so a real run shows where candidates drop out:

1. Grid-first seed: the area within the pre-filter distance of a 110 kV line, a GPZ or a 220/400 kV node,
   cut to the search area (usually one powiat).
2. Parcels: fetched by 1 km tiles that touch the seed (`fetch_parcels` is injected, so tests stay offline).
3. Clusters: adjacent parcels become sites. Clusters under `target.ha_min` are counted and dropped here;
   scoring them would only add rows that the area hard filter excludes anyway.
4. Score: features from the loaded layers, hard filters, weighted score.

Only the grid-first seed exists so far. The zoning seed waits for the `ru` connector. Layers that are not
loaded stay missing in the facts, never zero.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

import geopandas as gpd
from shapely import box, union_all
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import Prefilter, ScoringConfig
from dclh_pipeline.model.features import GridIndex, SiteLayers
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.scoring.run import ScoredSite, score_candidates
from dclh_pipeline.sources.egib import TILE_M, BBox, tile_bbox

M_PER_KM = 1000.0
BUFFER_SEGMENTS = 8  # quarter-circle segments; plenty for a 3–25 km buffer

ParcelFetcher = Callable[[list[BBox]], gpd.GeoDataFrame]


@dataclass
class SearchReport:
    seed_area_km2: float = 0.0
    tiles: int = 0
    parcels_fetched: int = 0
    parcels_in_seed: int = 0
    parcels_below_min: int = 0
    clusters: int = 0
    clusters_below_min_area: int = 0
    scored: int = 0
    excluded: int = 0
    seconds: dict[str, float] = field(default_factory=dict)

    def lines(self) -> list[tuple[str, str]]:
        return [
            ("Obszar zasilania (seed), km²", f"{self.seed_area_km2:,.0f}"),
            ("Kafelki 1 km do pobrania", str(self.tiles)),
            ("Działki pobrane", str(self.parcels_fetched)),
            ("Działki w obszarze seed", str(self.parcels_in_seed)),
            ("  pominięte (poniżej min. powierzchni działki)", str(self.parcels_below_min)),
            ("Klastry (lokalizacje)", str(self.clusters)),
            ("  poniżej minimalnej powierzchni", str(self.clusters_below_min_area)),
            ("Ocenione", str(self.scored)),
            ("  wykluczone twardymi filtrami", str(self.excluded)),
            *((f"Czas: {name}, s", f"{sec:.1f}") for name, sec in self.seconds.items()),
        ]


@dataclass(frozen=True)
class SearchResult:
    scored: list[ScoredSite]
    report: SearchReport


def grid_seed_area(area: BaseGeometry, grid: GridIndex, prefilter: Prefilter) -> BaseGeometry:
    """Union of the pre-filter buffers around 110 kV lines, GPZs and 220/400 kV nodes, cut to `area`.

    The same three distances `passes_grid_prefilter` uses, so the seed and the zone pre-filter agree.
    """
    buffers: list[BaseGeometry] = []
    for index, limit_km in (
        (grid.line110.geoms, prefilter.line110_km),
        (grid.gpz110.geoms, prefilter.gpz110_km),
        (grid.node.geoms, prefilter.node_220_400_km),
    ):
        # Only geometry that can reach the area matters, which keeps the 25 km node buffers cheap.
        reach = area.buffer(limit_km * M_PER_KM)
        buffers.extend(
            g.buffer(limit_km * M_PER_KM, quad_segs=BUFFER_SEGMENTS) for g in index.intersecting(reach)
        )
    if not buffers:
        return area.intersection(box(0, 0, 0, 0))  # empty
    return union_all(buffers).intersection(area)


def seed_tiles(seed: BaseGeometry, size_m: float = TILE_M) -> list[BBox]:
    """The aligned tiles that actually touch the seed (not its whole bounding box)."""
    if seed.is_empty:
        return []
    return [t for t in tile_bbox(seed.bounds, size_m) if seed.intersects(box(*t))]


def run_search(
    area: BaseGeometry,
    layers: SiteLayers,
    cfg: ScoringConfig,
    fetch_parcels: ParcelFetcher,
    *,
    parcels_as_of: datetime | None = None,
) -> SearchResult:
    """Search `area` (EPSG:2180) and return every scored site with the funnel counts."""
    if layers.grid is None:
        raise ValueError("search needs the grid layer: load OSM power first (dclh fetch osm_power)")
    report = SearchReport()

    started = time.monotonic()
    seed = grid_seed_area(area, layers.grid, cfg.prefilter)
    tiles = seed_tiles(seed)
    report.seed_area_km2 = seed.area / 1e6
    report.tiles = len(tiles)
    report.seconds["seed"] = time.monotonic() - started

    started = time.monotonic()
    fetched = fetch_parcels(tiles) if tiles else None
    report.seconds["parcels"] = time.monotonic() - started
    if fetched is None or fetched.empty:
        return SearchResult([], report)
    report.parcels_fetched = len(fetched)

    started = time.monotonic()
    in_seed = fetched[fetched.geometry.intersects(seed)]
    report.parcels_in_seed = len(in_seed)
    # No zoning layer yet, so zone_id is None. Small lots are dropped first and oversized clusters are cut,
    # otherwise adjacency chains a whole town into one site.
    big_enough = in_seed[in_seed.geometry.area / 10_000 >= cfg.search.min_parcel_ha]
    report.parcels_below_min = len(in_seed) - len(big_enough)
    parcels = [
        Parcel(str(pid), None, geom)
        for pid, geom in zip(big_enough["parcel_id"], big_enough.geometry, strict=True)
    ]
    sites = cluster_sites(
        parcels,
        mw_per_ha=cfg.target.mw_per_ha,
        max_site_ha=cfg.target.large_site_split_ha,
        min_piece_ha=cfg.target.ha_min,
    )
    report.clusters = len(sites)
    big = [replace(s, flags=[*s.flags, "seed_grid"]) for s in sites if s.area_ha >= cfg.target.ha_min]
    report.clusters_below_min_area = len(sites) - len(big)
    report.seconds["cluster"] = time.monotonic() - started

    started = time.monotonic()
    scored = score_candidates(big, layers, cfg, parcels_as_of=parcels_as_of)
    report.scored = len(scored)
    report.excluded = sum(1 for s in scored if s.score.excluded)
    report.seconds["score"] = time.monotonic() - started
    return SearchResult(scored, report)
