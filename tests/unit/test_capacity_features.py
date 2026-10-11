"""Phase 3 layers and features: DSO published capacity per matched GPZ, and PSE point lists.

Coordinates are metres (EPSG:2180), so the expected distances are exact.
"""

from __future__ import annotations

from datetime import UTC, datetime

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.capacity import build_gpz_capacity_layer, build_point_layer
from dclh_pipeline.model.features import (
    GpzCapacityLayer,
    PointLayer,
    SiteLayers,
    Substation,
    build_grid_index,
    build_site_features,
    closed_switchyard_fact,
    nearest_point_km_fact,
    published_headroom_fact,
)
from dclh_pipeline.model.gpz_match import gpz_candidates, normalise_name
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.types import GridBadge

CFG = load_scoring()
PIASKI = Point(1000.0, 1000.0)
ZABNO = Point(50_000.0, 1000.0)  # far from the Piaski site
AS_OF = pd.Timestamp("2026-09-01")


def _subs() -> list[Substation]:
    return [
        Substation(1, SubstationClass.GPZ_110_SN, (110_000, 15_000), PIASKI, name="GPZ Piaski"),
        Substation(2, SubstationClass.GPZ_110_SN, (110_000, 15_000), ZABNO, name="GPZ Żabno"),
    ]


def _capacity(rows: list[tuple[str, str, float | None]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dso": [r[0] for r in rows],
            "gpz_name": [r[1] for r in rows],
            "published_mw": [np.nan if r[2] is None else r[2] for r in rows],
            "as_of": [AS_OF] * len(rows),
            "note": [""] * len(rows),
        }
    )


def _layer(rows: list[tuple[str, str, float | None]], manual: dict[str, dict[str, int]] | None = None):
    layer, results = build_gpz_capacity_layer(
        _capacity(rows), gpz_candidates(_subs()), manual=manual, min_score=CFG.grid.gpz_match_min_score
    )
    return layer, results


def _site(x: float, y: float, size: float = 200.0) -> Site:
    geom = box(x - size / 2, y - size / 2, x + size / 2, y + size / 2)
    return Site(
        site_id=f"site-{x:.0f}-{y:.0f}",
        zone_id=None,
        parcel_ids=("p1",),
        geom=geom,
        area_ha=geom.area / 10_000,
        compactness=0.6,
        estimated_mw=geom.area / 10_000 * CFG.target.mw_per_ha,
        needs_split_suggestion=False,
    )


def _points(name_xy: list[tuple[str, float, float]], source_id: str = "pse_closed") -> PointLayer:
    frame = gpd.GeoDataFrame(
        {"name": [n for n, _, _ in name_xy], "as_of": [AS_OF] * len(name_xy)},
        geometry=[Point(x, y) for _, x, y in name_xy],
        crs="EPSG:2180",
    )
    return build_point_layer(frame, source_id)


# --- DSO published capacity -------------------------------------------------------------------------


def test_matched_gpz_carries_the_published_mw_with_provenance() -> None:
    layer, results = _layer([("enea", "GPZ Piaski", 12.5)])
    assert [r.method for r in results] == ["fuzzy"]
    (item,) = layer.items
    assert item.mw == 12.5
    assert item.source_id == "enea"
    assert item.as_of == datetime(2026, 9, 1, tzinfo=UTC)
    assert item.confidence is Confidence.ESTIMATED  # fuzzy match, not a manual mapping


def test_manual_mapping_gives_verified_confidence() -> None:
    manual = {"enea": {normalise_name("Piaski Stare"): 1}}
    layer, results = _layer([("enea", "Piaski Stare", 8.0)], manual=manual)
    assert results[0].method == "manual"
    assert layer.items[0].confidence is Confidence.VERIFIED


def test_unmatched_rows_are_kept_in_results_but_not_in_the_layer() -> None:
    layer, results = _layer([("enea", "GPZ Piaski", 5.0), ("enea", "Kraków Płaszów", 9.0)])
    assert len(layer.items) == 1
    assert results[1].candidate_key is None


def test_nearest_published_gpz_within_radius_gives_its_mw() -> None:
    layer, _ = _layer([("enea", "GPZ Piaski", 12.5)])
    fact = published_headroom_fact(_site(1000.0 + 1500, 1000.0).geom, layer, CFG)
    assert fact is not None
    assert fact.value == 12.5
    assert fact.source_id == "enea"


def test_published_zero_is_a_real_zero_not_missing() -> None:
    layer, _ = _layer([("enea", "GPZ Piaski", 0.0)])
    fact = published_headroom_fact(_site(1000.0, 1000.0).geom, layer, CFG)
    assert fact is not None
    assert fact.value == 0.0


def test_gpz_without_a_published_value_is_missing_not_zero() -> None:
    layer, _ = _layer([("enea", "GPZ Żabno", None)])
    assert published_headroom_fact(_site(ZABNO.x, ZABNO.y).geom, layer, CFG) is None


def test_gpz_beyond_the_radius_is_missing() -> None:
    layer, _ = _layer([("enea", "GPZ Piaski", 12.5)])
    far = _site(1000.0 + CFG.grid.published_gpz_km * 1000 + 2000, 1000.0)
    assert published_headroom_fact(far.geom, layer, CFG) is None


def test_empty_capacity_layer_is_missing() -> None:
    empty = GpzCapacityLayer.build([])
    assert published_headroom_fact(_site(0, 0).geom, empty, CFG) is None


def test_engine_reports_the_published_badge_when_headroom_is_known() -> None:
    layer, _ = _layer([("enea", "GPZ Piaski", 12.5)])
    layers = SiteLayers(grid=build_grid_index(_subs(), [], source_id="osm_power"), capacity=layer)
    features = build_site_features(_site(1200.0, 1000.0), layers, CFG)
    assert score_site(features, CFG).grid_badge is GridBadge.PUBLISHED


def test_engine_falls_back_to_the_proxy_badge_without_published_data() -> None:
    layers = SiteLayers(grid=build_grid_index(_subs(), [], source_id="osm_power"))
    features = build_site_features(_site(1200.0, 1000.0), layers, CFG)
    assert features.published_gpz_headroom_mw is None
    assert score_site(features, CFG).grid_badge is GridBadge.PROXY


# --- PSE point lists -------------------------------------------------------------------------------


def test_closed_switchyard_within_radius_is_true() -> None:
    layer = _points([("Rozdzielnia", 1000.0, 1000.0 + 2000)])
    fact = closed_switchyard_fact(_site(1000.0, 1000.0).geom, layer, CFG)
    assert fact is not None
    assert fact.value is True
    assert fact.source_id == "pse_closed"
    assert fact.confidence is Confidence.ESTIMATED


def test_closed_switchyard_beyond_radius_is_false_and_a_real_answer() -> None:
    far = 1000.0 + (CFG.grid.closed_switchyard_km + 1) * 1000
    layer = _points([("Rozdzielnia", far, 1000.0)])
    fact = closed_switchyard_fact(_site(1000.0, 1000.0).geom, layer, CFG)
    assert fact is not None
    assert fact.value is False


def test_closed_switchyard_is_missing_when_the_list_is_not_loaded() -> None:
    assert closed_switchyard_fact(_site(0, 0).geom, None, CFG) is None


def test_point_distance_is_in_km() -> None:
    layer = _points([("ODB A", 1000.0 + 10_000, 1000.0), ("ODB B", 1000.0 + 30_000, 1000.0)])
    fact = nearest_point_km_fact(_site(1000.0, 1000.0).geom, layer)
    assert fact is not None
    assert fact.value == pytest.approx(10.0 - 0.1)  # the site is 200 m wide: its edge is 100 m closer


def test_empty_point_list_gives_no_distance_rather_than_a_fake_one() -> None:
    empty = _points([])
    assert nearest_point_km_fact(_site(0, 0).geom, empty) is None
    assert nearest_point_km_fact(_site(0, 0).geom, None) is None


def test_point_layer_as_of_is_the_newest_row() -> None:
    frame = gpd.GeoDataFrame(
        {
            "name": ["a", "b"],
            "as_of": [pd.Timestamp("2026-08-01"), pd.Timestamp("2026-09-15")],
        },
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:2180",
    )
    layer = build_point_layer(frame, "pse_odb")
    assert layer.as_of == datetime(2026, 9, 15, tzinfo=UTC)


def test_build_site_features_fills_the_pse_facts_only_when_loaded() -> None:
    layers = SiteLayers(
        pse_closed=_points([("Rozdzielnia", 1000.0, 1000.0 + 500)]),
        pse_odb=_points([("ODB", 1000.0 + 5000, 1000.0)], source_id="pse_odb"),
    )
    features = build_site_features(_site(1000.0, 1000.0), layers, CFG)
    assert features.closed_switchyard_nearby is not None
    assert features.closed_switchyard_nearby.value is True
    assert features.dist_pse_odb_km is not None
    assert features.dist_pse_planned_station_km is None  # pse_avail not loaded
    assert features.published_gpz_headroom_mw is None  # no capacity layer
