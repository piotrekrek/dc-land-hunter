"""One search run for an area: the funnel from grid buffers to scored sites (PLAN.md, Phase 1.5).

Stages, each counted in the report so a real run shows where candidates drop out:

1. Grid-first seed: the area within the pre-filter distance of a 110 kV line, a GPZ or a 220/400 kV node,
   cut to the search area (usually one powiat). A parcel is kept only by the area holding its representative
   point, so neighbouring powiats never build a site from the same parcel.
2. Parcels: fetched by 1 km tiles that touch the seed (`fetch_parcels` is injected, so tests stay offline).
3. Clusters: adjacent parcels become sites. Clusters under `target.ha_min` are counted and dropped here;
   scoring them would only add rows that the area hard filter excludes anyway.
4. Score: features from the loaded layers, hard filters, weighted score.
5. Small plots: single parcels of `small.ha_min`–`small.ha_max` ha (not part of a site from step 3) inside the
   tighter grid buffers of `small.grid60`, scored with their own size range. Only those with a 60 MW grid tier
   are kept (`scoring/grid60.py`); they are stored with `kind = small`.

Only the grid-first seed exists so far; the zoning seed (land in a strefa gospodarcza far from the grid) is
not built. Zoning comes from the RU layers in `layers.zoning` and, for sites the hard filters keep, from
KIMPZP point samples (`sample_zoning`), which outrank the RU layers. Layers that are not loaded stay missing
in the facts, never zero.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

import geopandas as gpd
from shapely import box, union_all
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import Prefilter, ScoringConfig
from dclh_pipeline.model.features import GridIndex, SiteLayers, ZoningLayer
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Parcel, Site, cluster_sites, expansion_ha, parcel_site
from dclh_pipeline.scoring.grid60 import Grid60Tier
from dclh_pipeline.scoring.run import ScoredSite, score_candidates
from dclh_pipeline.scoring.types import Fact
from dclh_pipeline.sources.egib import TILE_M, BBox, tile_bbox

M_PER_KM = 1000.0
BUFFER_SEGMENTS = 8  # quarter-circle segments; plenty for a 3–25 km buffer

ParcelFetcher = Callable[[list[BBox]], gpd.GeoDataFrame]
# Asks KIMPZP for the given sites; returns their zoning layer and the count of answers by status.
ZoningSampler = Callable[[list[Site]], tuple[ZoningLayer, dict[str, int]]]
# Asks KIUG for the given sites; returns the estimated class III share per site_id and the answers by status.
SoilSampler = Callable[[list[Site]], tuple[dict[str, Fact[float]], dict[str, int]]]


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
    small_candidates: int = 0  # parcels in the small size range near enough to the grid
    small_kept: int = 0  # of those, with a 60 MW grid tier
    small_strong: int = 0
    kimpzp_answers: dict[str, int] = field(default_factory=dict)  # KIMPZP answers by status
    kiug_answers: dict[str, int] = field(default_factory=dict)  # KIUG soil-class answers by status
    zoning: dict[str, int] = field(default_factory=dict)  # kept (not excluded) sites by zoning value
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
            ("Małe działki (do 2 ha) blisko sieci", str(self.small_candidates)),
            ("  z oceną sieci 60 MW+", str(self.small_kept)),
            ("    w tym prawdopodobnie", str(self.small_strong)),
            *((f"KIMPZP odpowiedzi: {status}", str(n)) for status, n in sorted(self.kimpzp_answers.items())),
            *((f"KIUG odpowiedzi: {status}", str(n)) for status, n in sorted(self.kiug_answers.items())),
            *((f"Przeznaczenie (niewykluczone): {kind}", str(n)) for kind, n in sorted(self.zoning.items())),
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
    sample_zoning: ZoningSampler | None = None,
    sample_soil: SoilSampler | None = None,
) -> SearchResult:
    """Search `area` (EPSG:2180) and return every scored site with the funnel counts.

    `sample_zoning` and `sample_soil` are asked only about the sites the hard filters keep: an excluded site's
    zoning or soil changes nothing, and KIMPZP and KIUG each cost a request per sampled point. The soil share
    feeds only a penalty, so the rescore cannot exclude a kept site on it.
    """
    if layers.grid is None:
        raise ValueError("search needs the grid layer: load OSM power first (dclh fetch osm_power)")
    grid = layers.grid
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
    # A parcel on a powiat border touches two seeds. It belongs to the powiat holding its representative
    # point, so no parcel (and no site_id, which hashes the parcel set) is searched by two powiats.
    in_seed = fetched[
        fetched.geometry.intersects(seed) & fetched.geometry.representative_point().within(area)
    ]
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
        split_suggestion_ha=cfg.target.large_site_split_ha,
        max_site_ha=cfg.target.large_site_split_ha,
        min_piece_ha=cfg.target.ha_min,
    )
    report.clusters = len(sites)
    big = [replace(s, flags=[*s.flags, "seed_grid"]) for s in sites if s.area_ha >= cfg.target.ha_min]
    report.clusters_below_min_area = len(sites) - len(big)
    # Expansion land: every fetched parcel counts, also the small lots and other powiats' parcels.
    expansion = expansion_ha(big, [str(p) for p in fetched["parcel_id"]], list(fetched.geometry))
    layers = replace(
        layers,
        expansion_ha={
            sid: Fact(ha, "egib", parcels_as_of, Confidence.VERIFIED) for sid, ha in expansion.items()
        },
    )
    report.seconds["cluster"] = time.monotonic() - started

    started = time.monotonic()
    scored = score_candidates(big, layers, cfg, parcels_as_of=parcels_as_of)
    report.scored = len(scored)
    report.excluded = sum(1 for s in scored if s.score.excluded)
    report.seconds["score"] = time.monotonic() - started

    started = time.monotonic()
    small = small_candidates(in_seed, big, grid, cfg)
    report.small_candidates = len(small)
    scored_small = score_candidates(small, layers, cfg, parcels_as_of=parcels_as_of, kind="small")
    report.small_kept = len(scored_small)
    report.small_strong = sum(1 for s in scored_small if _strong(s))
    report.seconds["small"] = time.monotonic() - started

    # Small plots of the "possible" tier are many and need a phone call to the DSO anyway; sampling them would
    # cost a KIMPZP and a KIUG request each.
    kept = [s.site for s in scored if not s.score.excluded]
    kept_small = [s.site for s in scored_small if not s.score.excluded and _strong(s)]
    if (kept or kept_small) and (sample_zoning is not None or sample_soil is not None):
        sampled_layers = layers
        if sample_zoning is not None:
            started = time.monotonic()
            sampled, report.kimpzp_answers = sample_zoning(kept + kept_small)
            sampled_layers = replace(sampled_layers, zoning=(sampled, *layers.zoning))
            report.seconds["kimpzp"] = time.monotonic() - started
        if sample_soil is not None:
            started = time.monotonic()
            soil, report.kiug_answers = sample_soil(kept + kept_small)
            sampled_layers = replace(sampled_layers, soil_iii=soil)
            report.seconds["kiug"] = time.monotonic() - started
        rescored = score_candidates(kept, sampled_layers, cfg, parcels_as_of=parcels_as_of)
        rescored += score_candidates(
            kept_small, sampled_layers, cfg, parcels_as_of=parcels_as_of, kind="small"
        )
        by_id = {s.site.site_id: s for s in rescored}
        scored = [by_id.get(s.site.site_id, s) for s in scored]
        scored_small = [by_id.get(s.site.site_id, s) for s in scored_small]
    report.zoning = dict(
        Counter(
            s.features.zoning.value.value if s.features.zoning else "brak warstwy"
            for s in scored
            if not s.score.excluded
        )
    )
    return SearchResult(scored + scored_small, report)


def _strong(s: ScoredSite) -> bool:
    return s.grid60 is not None and s.grid60.value is Grid60Tier.STRONG


def small_candidates(
    parcels: gpd.GeoDataFrame, sites: list[Site], grid: GridIndex, cfg: ScoringConfig
) -> list[Site]:
    """Parcels of the small size range near enough to the grid for a tier, each as a site of its own.

    A parcel that is already part of a site of the minimum area stays in that site only.
    """
    g = cfg.small.grid60
    reach = Prefilter(
        line110_km=g.possible_line110_km, gpz110_km=g.possible_gpz110_km, node_220_400_km=g.strong_node_km
    )
    taken = {pid for s in sites for pid in s.parcel_ids}
    ha = parcels.geometry.area / 10_000
    sized = parcels[(ha >= cfg.small.ha_min) & (ha <= cfg.small.ha_max) & ~parcels["parcel_id"].isin(taken)]
    if sized.empty:
        return []
    near = grid_seed_area(union_all(list(sized.geometry)).envelope, grid, reach)
    return [
        parcel_site(Parcel(str(pid), None, geom), mw_per_ha=cfg.target.mw_per_ha)
        for pid, geom in zip(sized["parcel_id"], sized.geometry, strict=True)
        if geom.intersects(near)
    ]
