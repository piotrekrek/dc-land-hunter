"""Run connectors with graceful degradation (brief section 4).

A failing source never blocks a run: the last good snapshot is re-parsed and marked stale, and a
SOURCE_FAILURE event is raised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from dclh_pipeline.events import Event, EventType
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

log = logging.getLogger(__name__)


class RunStatus(StrEnum):
    OK = "ok"  # fresh snapshot fetched and parsed
    STALE = "stale"  # fetch or parse failed; last good snapshot used
    FAILED = "failed"  # failed and no usable snapshot exists


@dataclass
class SourceRunResult:
    source_id: str
    status: RunStatus
    snapshot: Snapshot | None = None
    table: pd.DataFrame | None = None
    error: str | None = None
    events: list[Event] = field(default_factory=list)

    @property
    def row_count(self) -> int | None:
        return None if self.table is None else len(self.table)


def run_source(source: Source, client: PoliteClient, store: SnapshotStore) -> SourceRunResult:
    try:
        snapshot = source.fetch(client, store)
        table = source.parse(snapshot)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.warning("source %s failed: %s", source.id, error)
        return _fallback(source, store, error)
    snapshot = store.mark_good(snapshot)
    return SourceRunResult(source.id, RunStatus.OK, snapshot, table)


def _fallback(source: Source, store: SnapshotStore, error: str) -> SourceRunResult:
    last_good = store.latest_good(source.id)
    event = Event(
        type=EventType.SOURCE_FAILURE,
        source_id=source.id,
        payload={
            "error": error,
            "fallback_snapshot": last_good.sha256 if last_good else None,
            "fallback_as_of": last_good.fetched_at.isoformat() if last_good else None,
        },
    )
    if last_good is None:
        return SourceRunResult(source.id, RunStatus.FAILED, error=error, events=[event])
    try:
        table = source.parse(last_good)
    except Exception as exc:  # the parser itself regressed
        error = f"{error}; fallback parse failed: {type(exc).__name__}: {exc}"
        return SourceRunResult(source.id, RunStatus.FAILED, error=error, events=[event])
    return SourceRunResult(source.id, RunStatus.STALE, last_good, table, error, [event])


def run_sources(sources: list[Source], client: PoliteClient, store: SnapshotStore) -> list[SourceRunResult]:
    return [run_source(s, client, store) for s in sources]
