"""Layer loading shared by the nightly powiat search and a single-parcel check (PLAN.md, Sprawdź działkę).

Two halves, so both callers use one implementation:

- **Static layers** come from saved snapshots and do not depend on where you look: the grid, gas pipelines
  and runways, residential-cluster proxies, protected areas, DSO and PSE capacity, heat, PSI, big cities.
- **Area layers** are downloaded for a bounding box: flood zones, mining areas, BDOT10k buildings and
  forest, and the RU zoning layers. A search asks for a whole powiat, a check for a parcel's surroundings.

A `StepReporter` hears about each area step. A search uses `strict=True`: a failed download fails the
search, because without it a hard filter would silently not apply. A check uses `strict=False`: a failed
step is reported and its layers stay `None`; the scorer then treats the criteria they feed as missing,
never as "fine".
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import geopandas as gpd
from shapely import box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import ScoringConfig, SourcesConfig
from dclh_pipeline.grid_capacity import GridCapacity, load_grid_capacity
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import (
    POWERED_BROWNFIELD_NEAR_M,
    GeomIndex,
    GridIndex,
    Layer,
    SiteLayers,
    ZoningLayer,
    build_grid_index,
)
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.psi import PsiLayer
from dclh_pipeline.model.sites import Site
from dclh_pipeline.model.zoning import mpzp_outline_kind, plan_ogolny_kind
from dclh_pipeline.scoring.types import Fact
from dclh_pipeline.settings import Settings
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.bdot10k import Bdot10kConnector
from dclh_pipeline.sources.egib import BBox
from dclh_pipeline.sources.gdos import GdosConnector, landscape_areas, protected_areas
from dclh_pipeline.sources.heat import heat_town_layer
from dclh_pipeline.sources.isok import (
    PROBABILITY_02PCT,
    PROBABILITY_1PCT,
    IsokConnector,
    zones_with_probability,
)
from dclh_pipeline.sources.kimpzp import KimpzpConnector, site_points
from dclh_pipeline.sources.kimpzp import zoning_layer as kimpzp_zoning_layer
from dclh_pipeline.sources.kiug import KiugConnector, site_soil_points, soil_iii_facts
from dclh_pipeline.sources.midas import MidasConnector
from dclh_pipeline.sources.osm_industrial import industrial_layer, read_industrial
from dclh_pipeline.sources.osm_infra import airport_ols_layer, gas_zone_layer, read_infra
from dclh_pipeline.sources.osm_power import OsmPowerConnector, to_grid
from dclh_pipeline.sources.osm_res import read_res, res_cluster_layer
from dclh_pipeline.sources.prg import PrgConnector, big_city_layer
from dclh_pipeline.sources.psi import load_psi_layer
from dclh_pipeline.sources.ru import RuMpzpConnector, RuPlanOgolnyConnector

# Area steps, in the order they run. Keys are what a reporter and the check page see.
STEP_FLOOD, STEP_MINING, STEP_BDOT10K, STEP_ZONING = "flood", "mining", "bdot10k", "zoning"


class StepReporter(Protocol):
    """Hears about each loading step. `result` is the loaded layer(s) on success, for building findings."""

    def start(self, key: str) -> None: ...

    def finish(self, key: str, *, ok: bool, result: Any = None, error: str | None = None) -> None: ...


class NullReporter:
    def start(self, key: str) -> None:
        pass

    def finish(self, key: str, *, ok: bool, result: Any = None, error: str | None = None) -> None:
        pass


@dataclass(frozen=True)
class StaticLayers:
    prg_snapshot: Snapshot
    osm_snapshot: Snapshot
    admin: gpd.GeoDataFrame  # PRG voivodeships, powiats and gminas
    grid: GridIndex
    gas: Layer
    ols: Layer
    res_clusters: Layer
    capacity: GridCapacity
    protected: Layer
    landscape: Layer
    heat: Layer | None
    psi: PsiLayer | None
    big_cities: Layer


@dataclass(frozen=True)
class AreaLayers:
    """Layers for one bounding box. `None`: the step failed or was skipped, so its facts stay missing."""

    flood: Layer | None = None  # 1% (and 10%) zones
    flood_02: Layer | None = None
    mining: dict[str, Layer] | None = None
    housing: Layer | None = None
    forest: Layer | None = None
    zoning: tuple[ZoningLayer, ...] = ()


def load_static_layers(
    settings: Settings, config: SourcesConfig, cfg: ScoringConfig, store: SnapshotStore
) -> StaticLayers:
    """Build the static layers from saved snapshots. Needs `prg`, `osm_power` and `gdos`; the rest
    is optional.

    DSO capacity and the PSE lists are used when a good snapshot exists (`grid_capacity.py`); heat
    and PSI when `dclh fetch heat|psi` has run. A source never fetched stays missing in the scores.
    """
    prg_snapshot, osm_snapshot = store.latest("prg"), store.latest("osm_power")
    gdos_snapshot = store.latest("gdos")
    # gdos is required too: without it the protected-area exclusion would silently never apply.
    for name, snap in (("prg", prg_snapshot), ("osm_power", osm_snapshot), ("gdos", gdos_snapshot)):
        if snap is None:
            raise ValueError(f"no saved snapshot of {name!r}; run `dclh fetch {name}` first")
    assert prg_snapshot is not None and osm_snapshot is not None and gdos_snapshot is not None

    admin = PrgConnector(config.get("prg")).parse(prg_snapshot)
    substations, lines = to_grid(OsmPowerConnector(config.get("osm_power")).parse(osm_snapshot))
    grid = build_grid_index(substations, lines, as_of=osm_snapshot.fetched_at, source_id="osm_power")
    # Gas pipelines and runways from the same extract: estimated proxies, no public source exists.
    infra = read_infra(osm_snapshot.path)
    gas_layer = gas_zone_layer(infra, cfg.proxies.gas_zone_m, osm_snapshot.fetched_at)
    ols_layer = airport_ols_layer(infra, cfg.proxies.runway_min_m, osm_snapshot.fetched_at)
    res_layer = res_cluster_layer(read_res(osm_snapshot.path), osm_snapshot.fetched_at)
    # DSO capacity and PSE lists: optional; a source never fetched or imported stays missing in the scores.
    grid_capacity = load_grid_capacity(store, config, substations, cfg)
    gdos = GdosConnector(config.get("gdos")).parse(gdos_snapshot)
    protected_layer = Layer(
        source_id="gdos",
        index=GeomIndex(protected_areas(gdos).geometry),
        as_of=gdos_snapshot.fetched_at,
        confidence=Confidence.VERIFIED,
    )
    # Penalty only: landscape parks and OChK must never reach the protected-area exclusion.
    landscape_layer = Layer(
        source_id="gdos",
        index=GeomIndex(landscape_areas(gdos).geometry),
        as_of=gdos_snapshot.fetched_at,
        confidence=Confidence.VERIFIED,
    )
    # Bonus layers from saved snapshots; a source never fetched stays missing.
    gminas = admin[admin["level"] == "gmina"]
    return StaticLayers(
        prg_snapshot=prg_snapshot,
        osm_snapshot=osm_snapshot,
        admin=admin,
        grid=grid,
        gas=gas_layer,
        ols=ols_layer,
        res_clusters=res_layer,
        capacity=grid_capacity,
        protected=protected_layer,
        landscape=landscape_layer,
        heat=heat_town_layer(store, config.get("heat"), gminas),
        psi=load_psi_layer(store, config.get("psi"), gminas),
        big_cities=big_city_layer(gminas, cfg.connectivity.cities, prg_snapshot.fetched_at),
    )


def powiat_unit(static: StaticLayers, teryt: str) -> Any:
    """The PRG row of a powiat (name, TERYT, geometry in EPSG:2180)."""
    admin = static.admin
    units = admin[
        (admin["level"] == "powiat") & (admin["teryt"].astype(str).str.lstrip("0") == teryt.lstrip("0"))
    ]
    if units.empty:
        raise ValueError(f"no powiat with TERYT {teryt!r} in the PRG snapshot")
    return units.iloc[0]


def load_industrial(static: StaticLayers, area: BaseGeometry) -> Layer | None:
    """Industrial and brownfield areas around `area`: powered brownfields and the zoning fallback."""
    bounds = tuple(area.buffer(POWERED_BROWNFIELD_NEAR_M).bounds)
    return industrial_layer(read_industrial(static.osm_snapshot.path, bounds), static.osm_snapshot.fetched_at)


@contextmanager
def _step(reporter: StepReporter, key: str, strict: bool) -> Iterator[Callable[[Any], None]]:
    """Report one step. Yields a setter for the result; a failure is re-raised only when `strict`."""
    reporter.start(key)
    box_: list[Any] = [None]
    try:
        yield lambda value: box_.__setitem__(0, value)
    except Exception as exc:
        reporter.finish(key, ok=False, error=f"{type(exc).__name__}: {exc}")
        if strict:
            raise
        return
    reporter.finish(key, ok=True, result=box_[0])


def load_area_layers(
    client: PoliteClient,
    config: SourcesConfig,
    store: SnapshotStore,
    bbox: BBox,
    teryt: str,
    *,
    powiat_area: BaseGeometry,
    reporter: StepReporter | None = None,
    strict: bool = True,
    clip: BBox | None = None,
) -> AreaLayers:
    """Download the area layers for `bbox`. BDOT10k is one zip per powiat; `clip` cuts its frames to a box.

    Flood zones (`isok`, 1% and 0.2%), mining areas (`midas`), residential buildings and forest
    (`bdot10k`) and RU plans (`ru`: MPZP outlines and plan ogólny zones) are downloaded now. With
    `strict` a failed download raises: without them the flood, mining and housing filters would
    silently not apply and zoning would read "unknown".
    """
    reporter = reporter or NullReporter()
    flood = flood_02 = housing = forest = None
    mining: dict[str, Layer] | None = None
    zoning: tuple[ZoningLayer, ...] = ()

    with _step(reporter, STEP_FLOOD, strict) as done:
        connector = IsokConnector(config.get("isok"), bbox=bbox)
        snapshot = connector.fetch(client, store)
        data = connector.parse(snapshot)
        store.mark_good(snapshot)
        flood = Layer(
            source_id="isok",
            index=GeomIndex(zones_with_probability(data, PROBABILITY_1PCT)),
            as_of=snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        flood_02 = Layer(
            source_id="isok",
            index=GeomIndex(zones_with_probability(data, PROBABILITY_02PCT)),
            as_of=snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        done({"flood_10_or_1": flood, "flood_0_2": flood_02})

    with _step(reporter, STEP_MINING, strict) as done:
        connector_m = MidasConnector(config.get("midas"), bbox=bbox)
        snapshot_m = connector_m.fetch(client, store)
        midas = connector_m.parse(snapshot_m)
        store.mark_good(snapshot_m)
        mining = {
            kind: Layer(
                source_id="midas",
                index=GeomIndex(midas.geometry[midas["kind"] == kind]),
                as_of=snapshot_m.fetched_at,
                confidence=Confidence.VERIFIED,
            )
            for kind in ("mining_area", "mining_terrain")
        }
        done(mining)

    with _step(reporter, STEP_BDOT10K, strict) as done:
        connector_b = Bdot10kConnector(config.get("bdot10k"), teryt=teryt)
        snapshot_b = connector_b.fetch(client, store)
        buildings = connector_b.parse(snapshot_b)
        store.mark_good(snapshot_b)
        # A box can't prove the axis order: the powiat's own buildings must lie in the powiat.
        if buildings.empty or not buildings.geometry.intersects(powiat_area).any():
            raise ValueError(f"BDOT10k: no residential building of powiat {teryt} lies inside it")
        trees = connector_b.parse_forest(snapshot_b)
        if clip is not None:
            window = box(*clip)
            buildings = buildings[buildings.geometry.intersects(window)]
            trees = trees[trees.geometry.intersects(window)]
        housing = Layer(
            source_id="bdot10k",
            index=GeomIndex(buildings.geometry),
            as_of=snapshot_b.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        forest = Layer(
            source_id="bdot10k",
            index=GeomIndex(trees.geometry),
            as_of=snapshot_b.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        done({"housing": housing, "forest": forest})

    with _step(reporter, STEP_ZONING, strict) as done:
        zoning = ru_zoning_layers(config, client, store, bbox)
        done(zoning)

    return AreaLayers(
        flood=flood, flood_02=flood_02, mining=mining, housing=housing, forest=forest, zoning=zoning
    )


def ru_zoning_layers(
    config: SourcesConfig, client: PoliteClient, store: SnapshotStore, bbox: BBox
) -> tuple[ZoningLayer, ...]:
    """The RU zoning layers of one bbox, in order of precedence: MPZP procedures, then plan ogólny zones.

    An MPZP outline in force has no designations, so it does not decide the zoning (KIMPZP does, ahead of
    these layers). Plan ogólny drafts count only as a procedure for a strefa gospodarcza
    (`plan_ogolny_kind`).
    """
    mpzp = RuMpzpConnector(config.get("ru"), bbox=bbox)
    pog = RuPlanOgolnyConnector(config.get("ru"), bbox=bbox)
    layers = []
    for connector in (mpzp, pog):
        snapshot = connector.fetch(client, store)
        table = connector.parse(snapshot)
        store.mark_good(snapshot)
        if connector is pog:
            kinds = [
                plan_ogolny_kind(z, s) for z, s in zip(table["zone_href"], table["status_href"], strict=True)
            ]
        else:
            kinds = [mpzp_outline_kind(s) for s in table["status_href"]]
        zones = tuple((k, g) for k, g in zip(kinds, table.geometry, strict=True) if k is not None)
        layers.append(ZoningLayer("ru", snapshot.fetched_at, zones))
    return tuple(layers)


def build_site_layers(static: StaticLayers, area: AreaLayers, industrial: Layer | None) -> SiteLayers:
    """Everything a site's features come from. A layer that is `None` stays out: its facts are missing."""
    constraints: dict[str, Layer] = {"protected_nature": static.protected}
    constraints.update(
        {
            key: layer
            for key, layer in (
                ("flood_10_or_1", area.flood),
                ("flood_0_2", area.flood_02),
                ("landscape_park_or_ochk", static.landscape),
                ("gas_pipeline_zone", static.gas),
                ("airport_ols", static.ols),
                ("forest", area.forest),
            )
            if layer is not None
        }
    )
    constraints.update(area.mining or {})
    return SiteLayers(
        grid=static.grid,
        zoning=area.zoning,
        residential=area.housing,
        industrial=industrial,
        constraints=constraints,
        capacity=static.capacity.capacity,
        pse_closed=static.capacity.pse_closed,
        pse_odb=static.capacity.pse_odb,
        pse_planned=static.capacity.pse_planned,
        heat=static.heat,
        psi=static.psi,
        big_cities=static.big_cities,
        res_clusters=static.res_clusters,
    )


_STATIC_CACHE: dict[str, Any] = {"key": None, "layers": None}


def snapshot_key(store: SnapshotStore) -> tuple[tuple[str, int, int], ...]:
    """What changed on disk: each source's manifest (name, mtime, size); `dclh fetch` appends a line."""
    root = store.root
    return tuple(
        sorted((m.parent.name, m.stat().st_mtime_ns, m.stat().st_size) for m in root.glob("*/manifest.jsonl"))
    )


def cached_static_layers(
    settings: Settings, config: SourcesConfig, cfg: ScoringConfig, store: SnapshotStore
) -> tuple[StaticLayers, bool]:
    """The static layers, built once per process and rebuilt when any snapshot changes.

    Returns (layers, from_cache). The check worker keeps them between jobs; a nightly search builds its own.
    """
    key = snapshot_key(store)
    if _STATIC_CACHE["layers"] is not None and _STATIC_CACHE["key"] == key:
        return _STATIC_CACHE["layers"], True
    layers = load_static_layers(settings, config, cfg, store)
    _STATIC_CACHE["key"], _STATIC_CACHE["layers"] = key, layers
    return layers, False


def zoning_sampler(
    client: PoliteClient, config: SourcesConfig, store: SnapshotStore
) -> Callable[[list[Site]], tuple[ZoningLayer, dict[str, int]]]:
    """KIMPZP point samples for the sites; a gmina server that is down leaves that point without a plan."""

    def sample(sites: list[Site]) -> tuple[ZoningLayer, dict[str, int]]:
        connector = KimpzpConnector(config.get("kimpzp"), points=site_points(sites))
        snapshot = connector.fetch(client, store)
        answers = connector.parse(snapshot)
        store.mark_good(snapshot)
        counts = {str(k): int(v) for k, v in answers["status"].value_counts().items()}
        return kimpzp_zoning_layer(sites, answers, snapshot.fetched_at), counts

    return sample


def soil_sampler(
    client: PoliteClient, config: SourcesConfig, store: SnapshotStore, cfg: ScoringConfig
) -> Callable[[list[Site]], tuple[dict[str, Fact[float]], dict[str, int]]]:
    """KIUG soil samples for the given sites (class I and II share)."""

    def sample(sites: list[Site]) -> tuple[dict[str, Fact[float]], dict[str, int]]:
        points = site_soil_points(sites, cfg.search.soil_points_per_site)
        connector = KiugConnector(config.get("kiug"), points=points)
        snapshot = connector.fetch(client, store)
        answers = connector.parse(snapshot)
        store.mark_good(snapshot)
        counts = {str(k): int(v) for k, v in answers["status"].value_counts().items()}
        return soil_iii_facts(answers, snapshot.fetched_at), counts

    return sample
