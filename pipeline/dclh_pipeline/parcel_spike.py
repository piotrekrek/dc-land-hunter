"""Timing spike for Sprawdź działkę (PLAN.md, step 1): how long does each area layer take for one parcel?

Run from a machine in Poland (`dclh check-spike`). Fetches every layer a check will need for the parcel's
surroundings, twice (the second pass reads the response cache: the warm cost), and records seconds, bytes,
rows and the process's peak memory per step. Nothing is written to the database. The numbers seed the
loader's default time estimates and decide whether the 30 s target is realistic.
"""

from __future__ import annotations

import resource
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pyproj import Transformer
from shapely import wkt
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import SourcesConfig
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.bdot10k import Bdot10kConnector
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.isok import IsokConnector
from dclh_pipeline.sources.kimpzp import KimpzpConnector
from dclh_pipeline.sources.kiug import KiugConnector
from dclh_pipeline.sources.midas import MidasConnector
from dclh_pipeline.sources.ru import RuMpzpConnector, RuPlanOgolnyConnector
from dclh_pipeline.sources.uldk import by_id_params, by_name_params, by_xy_params, lookup

AREA_BUFFER_M = 1_500.0  # the sheet's radius around the parcel
_TO_WGS = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class StepTiming:
    step: str
    pass_no: int  # 1 = cold (nothing cached), 2 = warm
    seconds: float
    detail: str
    ok: bool
    peak_rss_mb: float


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux reports KiB


def _timed(step: str, pass_no: int, fn: Callable[[], str]) -> StepTiming:
    started = time.monotonic()
    try:
        detail, ok = fn(), True
    except Exception as exc:  # a spike records failures instead of stopping at the first one
        detail, ok = f"{type(exc).__name__}: {exc}"[:200], False
    return StepTiming(
        step, pass_no, round(time.monotonic() - started, 2), detail, ok, round(_peak_rss_mb(), 1)
    )


def run_spike(
    client: PoliteClient,
    config: SourcesConfig,
    store: SnapshotStore,
    parcel_id: str,
    *,
    name_query: str | None = None,
) -> list[StepTiming]:
    """Time every step for `parcel_id`. `name_query` ("Konin 0002 1573") also tries the name search."""
    timings: list[StepTiming] = []
    parcels = lookup(client, by_id_params(parcel_id))
    if not parcels or parcels[0].wkt is None:
        raise ValueError(f"ULDK returned no geometry for {parcel_id}")
    geom: BaseGeometry = wkt.loads(parcels[0].wkt.split(";", 1)[-1])
    x, y = geom.representative_point().x, geom.representative_point().y
    lon, lat = _TO_WGS.transform(x, y)
    area = geom.buffer(AREA_BUFFER_M)
    x0, y0, x1, y1 = area.bounds
    bbox = (float(x0), float(y0), float(x1), float(y1))
    powiat = parcel_id[:4]  # TERYT of the powiat: the first four digits of the gmina code

    def bundle(connector: Any) -> str:
        snapshot = connector.fetch(client, store)
        table = connector.parse(snapshot)
        return f"{len(table)} rows, {snapshot.size_bytes} B"

    steps: list[tuple[str, Callable[[], str]]] = [
        ("uldk_by_id", lambda: f"{len(lookup(client, by_id_params(parcel_id)))} record(s)"),
        ("uldk_by_xy", lambda: f"{lookup(client, by_xy_params(x, y))[0].parcel_id}"),
        ("egib_neighbours", lambda: bundle(EgibConnector(config.get("egib"), bboxes=[bbox]))),
        ("isok_flood", lambda: bundle(IsokConnector(config.get("isok"), bbox=bbox))),
        ("midas_mining", lambda: bundle(MidasConnector(config.get("midas"), bbox=bbox))),
        ("ru_mpzp", lambda: bundle(RuMpzpConnector(config.get("ru"), bbox=bbox))),
        ("ru_plan_ogolny", lambda: bundle(RuPlanOgolnyConnector(config.get("ru"), bbox=bbox))),
        ("bdot10k_powiat", lambda: bundle(Bdot10kConnector(config.get("bdot10k"), teryt=powiat))),
        ("kimpzp_point", lambda: bundle(KimpzpConnector(config.get("kimpzp"), points=[("spike", lon, lat)]))),
        ("kiug_point", lambda: bundle(KiugConnector(config.get("kiug"), points=[("spike", x, y)]))),
    ]
    if name_query:
        steps.insert(
            2, ("uldk_by_name", lambda: f"{len(lookup(client, by_name_params(name_query)))} record(s)")
        )
    for pass_no in (1, 2):
        for step, fn in steps:
            timings.append(_timed(step, pass_no, fn))
    return timings


def summary_lines(timings: list[StepTiming]) -> list[str]:
    """Totals the loader's estimate starts from: cold and warm seconds, and the peak memory seen."""
    totals = {p: sum(t.seconds for t in timings if t.pass_no == p) for p in (1, 2)}
    failed = [f"{t.step} (pass {t.pass_no})" for t in timings if not t.ok]
    peak = max((t.peak_rss_mb for t in timings), default=0.0)
    return [
        f"cold total {totals[1]:.1f} s, warm total {totals[2]:.1f} s, peak RSS {peak:.0f} MB",
        f"failed steps: {', '.join(failed) if failed else 'none'}",
    ]
