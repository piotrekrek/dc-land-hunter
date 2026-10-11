"""Site features from geometry (brief section 5, steps 3–6): grid distances, zoning, brownfields, constraints.

Everything here is a pure function of EPSG:2180 geometry. Each computed value is a `Fact` that carries
its source_id, as_of and confidence. A layer with no data is `None` (missing), never zero. A loaded layer
with nothing near a site gives a real zero or False.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shapely import STRtree, unary_union
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import Prefilter, ScoringConfig
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.types import Fact, SiteFeatures, ZoningKind

V_110 = 110_000
V_220 = 220_000
M_PER_KM = 1000.0

# Constraint layers whose share of the site area is a feature (SiteFeatures field per layer name).
PCT_CONSTRAINTS: dict[str, str] = {
    "protected_nature": "protected_nature_pct",
    "flood_10_or_1": "flood_10_or_1_pct",
    "flood_0_2": "flood_0_2_pct",
    "mining_area": "mining_area_pct",
    "mining_terrain": "mining_terrain_pct",
    "forest": "forest_pct",
    "soil_class_i_ii": "soil_class_i_ii_pct",
    "soil_class_iii": "soil_class_iii_pct",
}
# Constraint layers that only matter as "touches the site or not".
BOOL_CONSTRAINTS: dict[str, str] = {
    "heritage_register": "on_heritage_register",
    "landscape_park_or_ochk": "landscape_park_or_ochk",
    "gas_pipeline_zone": "gas_pipeline_zone",
    "airport_ols": "airport_ols",
}

POWERED_BROWNFIELD_NEAR_M = 200.0  # industrial area this close to the site counts as the site's area
POWERED_BROWNFIELD_SUBSTATION_M = 100.0  # a 110 kV substation this close to that area makes it "powered"


class GeomIndex:
    """Geometries with an STRtree, so per-site queries stay fast over a whole AOI."""

    def __init__(self, geoms: Iterable[BaseGeometry]) -> None:
        self.geoms: tuple[BaseGeometry, ...] = tuple(geoms)
        self._tree: STRtree | None = STRtree(self.geoms) if self.geoms else None

    def __len__(self) -> int:
        return len(self.geoms)

    def nearest_distance_m(self, geom: BaseGeometry) -> float | None:
        """Distance to the nearest geometry in metres (0 when they touch), or None for an empty index."""
        if self._tree is None:
            return None
        return float(geom.distance(self.geoms[int(self._tree.nearest(geom))]))

    def indices_within_m(self, geom: BaseGeometry, radius_m: float) -> list[int]:
        """Positions of the geometries within `radius_m` metres of `geom`."""
        if self._tree is None:
            return []
        candidates = self._tree.query(geom.buffer(radius_m), predicate="intersects")
        return [int(i) for i in candidates if geom.distance(self.geoms[int(i)]) <= radius_m]

    def within_m(self, geom: BaseGeometry, radius_m: float) -> list[BaseGeometry]:
        return [self.geoms[i] for i in self.indices_within_m(geom, radius_m)]

    def intersecting(self, geom: BaseGeometry) -> list[BaseGeometry]:
        if self._tree is None:
            return []
        return [self.geoms[int(i)] for i in self._tree.query(geom, predicate="intersects")]


@dataclass(frozen=True)
class Layer:
    """Geometries from one source, with the provenance every derived value needs."""

    source_id: str
    index: GeomIndex
    as_of: datetime | None = None
    confidence: Confidence = Confidence.ESTIMATED


@dataclass(frozen=True)
class Substation:
    osm_id: int
    cls: SubstationClass
    voltages: tuple[int, ...]
    geom: BaseGeometry  # Point, EPSG:2180
    name: str | None = None  # OSM `name` tag, used to match DSO capacity lists to GPZ


@dataclass(frozen=True)
class Line:
    osm_id: int
    voltage: int | None  # highest voltage in volts; None when the tag is missing
    geom: BaseGeometry  # LineString, EPSG:2180


@dataclass(frozen=True)
class GridIndex:
    """The OSM grid, indexed by the classes the scorer needs. Built once per run."""

    source_id: str
    as_of: datetime | None
    confidence: Confidence
    line110: _IdIndex
    gpz110: _IdIndex
    hv110_substations: _IdIndex
    node: _IdIndex


@dataclass(frozen=True)
class ZoningLayer:
    """Planning zones (RU: MPZP and plan ogólny), each already mapped to a `ZoningKind`."""

    source_id: str
    as_of: datetime | None
    zones: tuple[tuple[ZoningKind, BaseGeometry], ...]


@dataclass(frozen=True)
class GpzCapacityItem:
    """One DSO GPZ with a matched location.

    `mw` is the published headroom, or None when nothing is published.
    """

    geom: BaseGeometry  # Point, EPSG:2180 (the matched OSM substation)
    source_id: str  # the DSO id, e.g. "enea"
    gpz_name: str
    mw: float | None
    as_of: datetime | None
    confidence: Confidence  # verified for a manual mapping, estimated for a fuzzy match


@dataclass(frozen=True)
class GpzCapacityLayer:
    """Published DSO capacity per matched GPZ (Phase 3). Only matched GPZs are in the layer."""

    items: tuple[GpzCapacityItem, ...]
    index: GeomIndex

    @classmethod
    def build(cls, items: Sequence[GpzCapacityItem]) -> GpzCapacityLayer:
        return cls(tuple(items), GeomIndex(i.geom for i in items))


@dataclass(frozen=True)
class PointLayer:
    """Points from one PSE list (closed switchyards, ODB entries, planned load stations), EPSG:2180."""

    source_id: str
    as_of: datetime | None
    confidence: Confidence
    index: GeomIndex


class _IdIndex:
    """A GeomIndex whose items carry the OSM id, so distinct lines can be counted."""

    def __init__(self, items: Sequence[tuple[int, BaseGeometry]]) -> None:
        self.ids: tuple[int, ...] = tuple(i for i, _ in items)
        self.geoms = GeomIndex(g for _, g in items)

    def __len__(self) -> int:
        return len(self.geoms)

    def ids_within_m(self, geom: BaseGeometry, radius_m: float) -> set[int]:
        return {self.ids[i] for i in self.geoms.indices_within_m(geom, radius_m)}


def build_grid_index(
    substations: Iterable[Substation],
    lines: Iterable[Line],
    *,
    source_id: str = "osm_power",
    as_of: datetime | None = None,
    confidence: Confidence = Confidence.ESTIMATED,
) -> GridIndex:
    subs = list(substations)
    return GridIndex(
        source_id=source_id,
        as_of=as_of,
        confidence=confidence,
        line110=_IdIndex(
            [(ln.osm_id, ln.geom) for ln in lines if ln.voltage is not None and V_110 <= ln.voltage < V_220]
        ),
        gpz110=_IdIndex([(s.osm_id, s.geom) for s in subs if s.cls is SubstationClass.GPZ_110_SN]),
        hv110_substations=_IdIndex(
            [(s.osm_id, s.geom) for s in subs if any(V_110 <= v < V_220 for v in s.voltages)]
        ),
        node=_IdIndex([(s.osm_id, s.geom) for s in subs if s.cls is SubstationClass.NODE_220_400]),
    )


def _fact[T](value: T, source_id: str, as_of: datetime | None, confidence: Confidence) -> Fact[T]:
    return Fact(value=value, source_id=source_id, as_of=as_of, confidence=confidence)


def passes_grid_prefilter(geom: BaseGeometry, grid: GridIndex, prefilter: Prefilter) -> bool:
    """Brief step 4: keep a zone if a 110 kV line, a GPZ or a 220/400 kV node is near enough (any one)."""
    checks = (
        (grid.line110.geoms, prefilter.line110_km),
        (grid.gpz110.geoms, prefilter.gpz110_km),
        (grid.node.geoms, prefilter.node_220_400_km),
    )
    for index, limit_km in checks:
        distance = index.nearest_distance_m(geom)
        if distance is not None and distance <= limit_km * M_PER_KM:
            return True
    return False


def grid_facts(
    site: BaseGeometry, grid: GridIndex, cfg: ScoringConfig
) -> dict[str, Fact[float] | Fact[int] | None]:
    """Distances to the nearest 110 kV line, GPZ and 220/400 kV node, and the N-1 line count."""
    src, as_of, conf = grid.source_id, grid.as_of, grid.confidence

    def km(index: _IdIndex) -> Fact[float] | None:
        distance = index.geoms.nearest_distance_m(site)
        return None if distance is None else _fact(distance / M_PER_KM, src, as_of, conf)

    radius = cfg.grid.two_independent_110_lines_within_km * M_PER_KM
    n1 = _fact(len(grid.line110.ids_within_m(site, radius)), src, as_of, conf) if len(grid.line110) else None
    return {
        "dist_line110_km": km(grid.line110),
        "dist_gpz110_km": km(grid.gpz110),
        "dist_node_220_400_km": km(grid.node),
        "independent_110_lines_near": n1,
    }


def powered_brownfield(site: BaseGeometry, industrial: Layer | None, grid: GridIndex) -> Fact[bool] | None:
    """Brief section 3: an industrial or brownfield area near the site with its own 110 kV substation.

    Missing (None) when either the industrial layer or the grid is not loaded; False when both are loaded and
    no such area exists.
    """
    if industrial is None or not len(grid.hv110_substations):
        return None
    for area in industrial.index.within_m(site, POWERED_BROWNFIELD_NEAR_M):
        if grid.hv110_substations.geoms.within_m(area, POWERED_BROWNFIELD_SUBSTATION_M):
            return _fact(True, grid.source_id, grid.as_of, grid.confidence)
    return _fact(False, grid.source_id, grid.as_of, grid.confidence)


def zoning_fact(
    site: BaseGeometry, zoning: ZoningLayer | None, industrial: Layer | None
) -> Fact[ZoningKind] | None:
    """The zone covering most of the site; else an industrial area as `unknown`; else `unknown`, missing.

    Returns None only when no planning layer is loaded at all. A loaded register without a plan is
    "unknown", not "bad" (brief 3, R3), so it is never counted as a known plan.
    """
    if zoning is not None:
        best: tuple[float, ZoningKind] | None = None
        for kind, zone in zoning.zones:
            if not zone.intersects(site):
                continue
            overlap = zone.intersection(site).area
            if best is None or overlap > best[0]:
                best = (overlap, kind)
        if best is not None:
            return _fact(best[1], zoning.source_id, zoning.as_of, Confidence.VERIFIED)
    if industrial is not None and industrial.index.intersecting(site):
        return _fact(ZoningKind.UNKNOWN, industrial.source_id, industrial.as_of, Confidence.ESTIMATED)
    if zoning is not None:
        source = zoning.source_id
        return _fact(ZoningKind.UNKNOWN, source, zoning.as_of, Confidence.MISSING)
    return None


def overlap_pct(site: BaseGeometry, geoms: Sequence[BaseGeometry]) -> float:
    """Share of the site's area (0–100) covered by the given geometries."""
    hits = [g for g in geoms if g.intersects(site)]
    if not hits or site.area == 0:
        return 0.0
    covered = unary_union(hits).intersection(site).area
    return float(min(100.0, 100.0 * covered / site.area))


def constraint_facts(
    site: BaseGeometry, constraints: Mapping[str, Layer]
) -> dict[str, Fact[float] | Fact[bool] | None]:
    """Percent-overlap and touch facts for each constraint layer that is loaded (SiteFeatures fields)."""
    out: dict[str, Fact[float] | Fact[bool] | None] = {}
    for name, field_name in PCT_CONSTRAINTS.items():
        layer = constraints.get(name)
        if layer is None:
            continue
        pct = overlap_pct(site, layer.index.intersecting(site))
        out[field_name] = _fact(pct, layer.source_id, layer.as_of, layer.confidence)
    for name, field_name in BOOL_CONSTRAINTS.items():
        layer = constraints.get(name)
        if layer is None:
            continue
        out[field_name] = _fact(
            bool(layer.index.intersecting(site)), layer.source_id, layer.as_of, layer.confidence
        )
    return out


def residential_fact(site: BaseGeometry, residential: Layer | None) -> Fact[float] | None:
    """Distance to the nearest residential building, in metres."""
    if residential is None:
        return None
    distance = residential.index.nearest_distance_m(site)
    if distance is None:
        return None
    return _fact(distance, residential.source_id, residential.as_of, residential.confidence)


def published_headroom_fact(
    site: BaseGeometry, layer: GpzCapacityLayer, cfg: ScoringConfig
) -> Fact[float] | None:
    """Published MW of the nearest GPZ (within `published_gpz_km`) that has a published value.

    Missing (None) when no such GPZ exists. A GPZ with a published 0 MW is a real zero, not missing.
    """
    radius = cfg.grid.published_gpz_km * M_PER_KM
    candidates = [
        layer.items[i] for i in layer.index.indices_within_m(site, radius) if layer.items[i].mw is not None
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda item: site.distance(item.geom))
    if nearest.mw is None:  # unreachable: candidates above already have a value
        return None
    return _fact(nearest.mw, nearest.source_id, nearest.as_of, nearest.confidence)


def closed_switchyard_fact(
    site: BaseGeometry, layer: PointLayer | None, cfg: ScoringConfig
) -> Fact[bool] | None:
    """True when a closed PSE switchyard is within `closed_switchyard_km`.

    Missing when the list is not loaded.
    """
    if layer is None:
        return None
    hit = bool(layer.index.indices_within_m(site, cfg.grid.closed_switchyard_km * M_PER_KM))
    return _fact(hit, layer.source_id, layer.as_of, layer.confidence)


def nearest_point_km_fact(site: BaseGeometry, layer: PointLayer | None) -> Fact[float] | None:
    """Distance in km to the nearest point of a PSE list. Missing when the list is not loaded or empty."""
    if layer is None:
        return None
    distance = layer.index.nearest_distance_m(site)
    return (
        None
        if distance is None
        else _fact(distance / M_PER_KM, layer.source_id, layer.as_of, layer.confidence)
    )


@dataclass(frozen=True)
class SiteLayers:
    """Everything a site's features come from. A None layer means "not loaded", so its facts are missing."""

    grid: GridIndex | None = None
    zoning: ZoningLayer | None = None
    industrial: Layer | None = None  # OSM / BDOT10k industrial and brownfield areas
    residential: Layer | None = None  # residential buildings
    constraints: dict[str, Layer] = field(default_factory=dict)
    capacity: GpzCapacityLayer | None = None  # DSO published capacity per matched GPZ
    pse_closed: PointLayer | None = None  # PSE closed switchyards (no connection possible)
    pse_odb: PointLayer | None = None  # PSE connection conditions issued (demand signal)
    pse_planned: PointLayer | None = None  # PSE planned load stations


def build_site_features(
    site: Site,
    layers: SiteLayers,
    cfg: ScoringConfig,
    *,
    parcels_source_id: str = "egib",
    parcels_as_of: datetime | None = None,
) -> SiteFeatures:
    """Compute every feature the scorer knows how to use from one candidate site and the loaded layers."""
    features: dict[str, Any] = {
        "area_ha": _fact(site.area_ha, parcels_source_id, parcels_as_of, Confidence.VERIFIED),
        "compactness": _fact(site.compactness, parcels_source_id, parcels_as_of, Confidence.VERIFIED),
        "parcel_count": _fact(len(site.parcel_ids), parcels_source_id, parcels_as_of, Confidence.VERIFIED),
    }
    if layers.grid is not None:
        features.update(grid_facts(site.geom, layers.grid, cfg))
        features["powered_brownfield"] = powered_brownfield(site.geom, layers.industrial, layers.grid)
    if layers.capacity is not None:
        features["published_gpz_headroom_mw"] = published_headroom_fact(site.geom, layers.capacity, cfg)
    features["closed_switchyard_nearby"] = closed_switchyard_fact(site.geom, layers.pse_closed, cfg)
    features["dist_pse_odb_km"] = nearest_point_km_fact(site.geom, layers.pse_odb)
    features["dist_pse_planned_station_km"] = nearest_point_km_fact(site.geom, layers.pse_planned)
    features["zoning"] = zoning_fact(site.geom, layers.zoning, layers.industrial)
    features["residential_min_m"] = residential_fact(site.geom, layers.residential)
    features.update(constraint_facts(site.geom, layers.constraints))
    return SiteFeatures(**features)
