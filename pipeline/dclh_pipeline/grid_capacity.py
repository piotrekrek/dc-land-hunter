"""Load the Phase 3 grid layers (DSO capacity, PSE lists) from saved snapshots, for search and the report.

Each source uses its newest good snapshot, whichever way it got there: `dclh fetch tauron|energa` (the
connectors) or `dclh import-manual <source> <file>` (the owner's file). A source with no snapshot leaves
its layer unloaded, so its facts stay missing in the scores, never zero.

- DSO capacity (`enea`, `energa`, `tauron`) becomes one capacity layer. Tauron publishes locations; the
  others are matched to OSM GPZs by name (`data/manual/gpz_mapping.csv` wins over fuzzy matching).
- `pse_closed` and `pse_odb` name PSE stations; they are located on the named 220/400 kV nodes in OSM.
- `pse_avail` (planned stations) is typed in with coordinates by hand and used as it is.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dclh_pipeline.config import ScoringConfig, SourcesConfig
from dclh_pipeline.model.capacity import build_gpz_capacity_layer, build_point_layer, locate_points
from dclh_pipeline.model.features import GpzCapacityLayer, PointLayer, Substation
from dclh_pipeline.model.gpz_match import MatchResult, gpz_candidates, node_candidates, station_points
from dclh_pipeline.settings import REPO_ROOT
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.energa import EnergaConnector
from dclh_pipeline.sources.manual import MANUAL_CAPACITY, MANUAL_POINTS, load_gpz_mapping
from dclh_pipeline.sources.tauron import TauronConnector

log = logging.getLogger(__name__)

GPZ_MAPPING = REPO_ROOT / "data" / "manual" / "gpz_mapping.csv"
DSO_SOURCES = ("enea", "energa", "tauron")


@dataclass(frozen=True)
class GridCapacity:
    capacity: GpzCapacityLayer | None = None
    pse_closed: PointLayer | None = None
    pse_odb: PointLayer | None = None
    pse_planned: PointLayer | None = None
    results: list[MatchResult] = field(default_factory=list)  # every DSO name and PSE station, for the report
    snapshots: dict[str, Snapshot] = field(default_factory=dict)  # the snapshot each loaded source came from


def _parse_capacity(source_id: str, snapshot: Snapshot, sources: SourcesConfig) -> pd.DataFrame:
    """The connector's own format (Tauron JSON, Energa PDF) or the owner's canonical CSV/XLSX."""
    spec = sources.get(source_id)
    suffix = snapshot.path.suffix.lower()
    if source_id == "tauron" and suffix == ".json":
        return TauronConnector(spec).parse(snapshot)
    if source_id == "energa" and suffix == ".pdf":
        return EnergaConnector(spec).parse(snapshot)
    return MANUAL_CAPACITY[source_id](spec, snapshot.path).parse(snapshot)


def load_grid_capacity(
    store: SnapshotStore,
    sources: SourcesConfig,
    substations: Sequence[Substation],
    cfg: ScoringConfig,
    *,
    mapping_path: Path = GPZ_MAPPING,
) -> GridCapacity:
    min_score = cfg.grid.gpz_match_min_score
    snapshots: dict[str, Snapshot] = {}
    tables: list[pd.DataFrame] = []
    for source_id in DSO_SOURCES:
        snapshot = store.latest_good(source_id)
        if snapshot is None:
            continue
        tables.append(_parse_capacity(source_id, snapshot, sources))
        snapshots[source_id] = snapshot

    results: list[MatchResult] = []
    capacity: GpzCapacityLayer | None = None
    if tables:
        manual = load_gpz_mapping(mapping_path) if mapping_path.is_file() else {}
        capacity, matched = build_gpz_capacity_layer(
            pd.concat(tables, ignore_index=True),
            gpz_candidates(substations),
            manual=manual,
            stations=station_points(substations),
            min_score=min_score,
        )
        results.extend(matched)

    nodes = node_candidates(substations)
    points: dict[str, PointLayer] = {}
    for source_id in ("pse_closed", "pse_odb", "pse_avail"):
        snapshot = store.latest_good(source_id)
        if snapshot is None:
            continue
        table = MANUAL_POINTS[source_id](sources.get(source_id), snapshot.path).parse(snapshot)
        if source_id != "pse_avail":  # planned stations are not built yet, so OSM cannot have them
            table, located = locate_points(table, source_id, nodes, min_score=min_score)
            results.extend(located)
        points[source_id] = build_point_layer(table, source_id)
        snapshots[source_id] = snapshot
    log.info("grid capacity layers loaded from %s", sorted(snapshots))
    return GridCapacity(
        capacity=capacity,
        pse_closed=points.get("pse_closed"),
        pse_odb=points.get("pse_odb"),
        pse_planned=points.get("pse_avail"),
        results=results,
        snapshots=snapshots,
    )
