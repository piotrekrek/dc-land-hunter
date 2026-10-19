"""Build the Phase 3 layers from parsed DSO capacity and PSE point tables (plan Phase 3).

Inputs are the tables that the manual connectors in `sources/manual.py` return. Nothing here reads files
or the network, so the same code runs on the VPS and in tests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from dclh_pipeline.model.features import GeomIndex, GpzCapacityItem, GpzCapacityLayer, PointLayer
from dclh_pipeline.model.gpz_match import GpzCandidate, MatchResult, match_gpz_names
from dclh_pipeline.model.provenance import Confidence


def build_gpz_capacity_layer(
    capacity: pd.DataFrame,
    candidates: Sequence[GpzCandidate],
    *,
    manual: Mapping[str, Mapping[str, int]] | None = None,
    stations: Sequence[GpzCandidate] = (),
    min_score: float = 90.0,
) -> tuple[GpzCapacityLayer, list[MatchResult]]:
    """Match each DSO GPZ row to a substation and keep the matched ones.

    `capacity` has the columns `dso`, `gpz_name`, `published_mw` (NaN when nothing is published) and `as_of`.
    Rows that also carry `x_2180`/`y_2180` (the DSO publishes the location, as Tauron does) are placed there
    without name matching, at verified confidence.
    `manual` is keyed by DSO id, then by the normalised DSO GPZ name (see `load_gpz_mapping`). `stations` are
    all substations (`station_points`): a manual mapping may point at any of them, named or not.
    Returns the layer and one `MatchResult` per input row, so the owner can see every miss and its reason.
    """
    manual = manual or {}
    by_key = {c.key: c for c in stations} | {c.key: c for c in candidates}
    items: list[GpzCapacityItem] = []
    results: list[MatchResult] = []
    if {"x_2180", "y_2180"} <= set(capacity.columns):
        located = capacity["x_2180"].notna() & capacity["y_2180"].notna()
        for rec in capacity[located].to_dict("records"):
            items.append(
                GpzCapacityItem(
                    geom=Point(float(rec["x_2180"]), float(rec["y_2180"])),
                    source_id=str(rec["dso"]),
                    gpz_name=str(rec["gpz_name"]),
                    mw=None if pd.isna(rec["published_mw"]) else float(rec["published_mw"]),
                    as_of=_as_datetime(rec["as_of"]),
                    confidence=Confidence.VERIFIED,
                )
            )
            results.append(
                MatchResult(
                    str(rec["dso"]),
                    str(rec["gpz_name"]),
                    None,
                    100.0,
                    "published",
                    "lokalizacja podana przez OSD",
                )
            )
        capacity = capacity[~located]
    for dso, rows in capacity.groupby("dso", sort=True):
        matched = match_gpz_names(
            str(dso),
            list(rows["gpz_name"]),
            candidates,
            manual=manual.get(str(dso)),
            manual_targets=by_key.keys(),
            min_score=min_score,
        )
        results.extend(matched)
        names, mws, dates = list(rows["gpz_name"]), list(rows["published_mw"]), list(rows["as_of"])
        for name, published, as_of, result in zip(names, mws, dates, matched, strict=True):
            if result.candidate_key is None:
                continue
            items.append(
                GpzCapacityItem(
                    geom=by_key[result.candidate_key].geom,
                    source_id=str(dso),
                    gpz_name=str(name),
                    mw=None if pd.isna(published) else float(published),
                    as_of=_as_datetime(as_of),
                    confidence=Confidence.VERIFIED if result.method == "manual" else Confidence.ESTIMATED,
                )
            )
    return GpzCapacityLayer.build(items), results


def locate_points(
    points: gpd.GeoDataFrame,
    source_id: str,
    candidates: Sequence[GpzCandidate],
    *,
    min_score: float = 90.0,
) -> tuple[gpd.GeoDataFrame, list[MatchResult]]:
    """Give the rows without a geometry the location of the station they name (a PSE list has no coordinates).

    `candidates` are the stations to look in (for PSE lists, the named 220/400 kV nodes). Rows that already
    have a geometry are kept as they are. Rows whose name matches no station clearly are dropped and come
    back as unmatched results, so the report shows them. Each distinct name is matched once.
    """
    missing = points.geometry.isna() | points.geometry.is_empty
    names = sorted(set(points.loc[missing, "name"]))
    results = match_gpz_names(source_id, names, candidates, min_score=min_score)
    by_key = {c.key: c.geom for c in candidates}
    where = {r.dso_gpz_name: by_key[r.candidate_key] for r in results if r.candidate_key is not None}
    located = points.copy()
    located.loc[missing, "geometry"] = [where.get(n) for n in points.loc[missing, "name"]]
    keep = located.geometry.notna()
    return gpd.GeoDataFrame(located[keep], geometry="geometry", crs=points.crs), results


def build_point_layer(points: gpd.GeoDataFrame, source_id: str) -> PointLayer:
    """Index one PSE point list. An empty list still gives a layer, so "nothing listed" is a real answer."""
    as_of = _as_datetime(points["as_of"].max()) if len(points) else None
    return PointLayer(
        source_id=source_id,
        as_of=as_of,
        confidence=Confidence.ESTIMATED,  # coordinates from a list are approximate
        index=GeomIndex(points.geometry),
    )


def _as_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.to_pydatetime()
