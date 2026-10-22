"""conn_city: the configured big cities are placed by their PRG urban gmina; a site gets the km to the nearest."""

from __future__ import annotations

from datetime import UTC, datetime

import geopandas as gpd
import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import ScoringConfig, load_scoring
from dclh_pipeline.model.features import SiteLayers, big_city_km_fact, build_site_features
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.sources.prg import big_city_layer

CFG: ScoringConfig = load_scoring()
AS_OF = datetime(2026, 10, 12, tzinfo=UTC)


def _square(x: float, y: float, size: float = 1000.0) -> BaseGeometry:
    return box(x, y, x + size, y + size)


def _gminas() -> gpd.GeoDataFrame:
    """Two big cities as urban gminas, and a rural gmina that shares a big city's name and must not be placed."""
    rows = [
        ("3064011", "Poznań", _square(0, 0)),  # urban (type 1)
        ("1061011", "Łódź", _square(100_000, 0)),  # urban (type 1)
        ("0000002", "Opole", _square(50_000, 0)),  # rural (type 2) with a big city's name: not placed
    ]
    return gpd.GeoDataFrame(
        {"teryt": [r[0] for r in rows], "name": [r[1] for r in rows]},
        geometry=[r[2] for r in rows],
        crs="EPSG:2180",
    )


def test_distance_is_zero_inside_a_city_and_km_to_the_nearest_outside() -> None:
    layer = big_city_layer(_gminas(), ["Poznań", "Łódź", "Opole"], AS_OF)
    inside = big_city_km_fact(_square(100, 100, 200), layer)
    assert inside is not None and inside.value == 0.0
    # 49 km east of Poznań's square and 50 km west of Łódź's; the rural "Opole" at x=50 km is ignored.
    between = big_city_km_fact(box(50_000, 0, 50_100, 100), layer)
    assert between is not None and between.value == pytest.approx(49.0)
    assert (between.source_id, between.as_of, between.confidence) == ("prg", AS_OF, Confidence.VERIFIED)


def test_no_layer_or_no_placed_city_is_missing() -> None:
    assert big_city_km_fact(_square(0, 0), None) is None
    nothing_placed = big_city_layer(_gminas(), ["Opole", "Gniezno"], AS_OF)
    assert big_city_km_fact(_square(0, 0), nothing_placed) is None


def test_the_configured_list_reaches_the_conn_city_score() -> None:
    assert {"Poznań", "Łódź"} <= set(CFG.connectivity.cities)
    site = Site(
        site_id="t",
        zone_id=None,
        parcel_ids=("p1",),
        geom=box(50_000, 0, 50_100, 100),
        area_ha=12.0,
        compactness=0.8,
        estimated_mw=72.0,
        needs_split_suggestion=False,
    )
    layer = big_city_layer(_gminas(), CFG.connectivity.cities, AS_OF)
    features = build_site_features(site, SiteLayers(big_cities=layer), CFG)
    assert features.dist_big_city_km is not None and features.dist_big_city_km.value == pytest.approx(49.0)
    missing = build_site_features(site, SiteLayers(), CFG)
    assert missing.dist_big_city_km is None
    item = next(i for i in score_site(features, CFG).items if i.key == "conn_city")
    assert item.confidence == Confidence.VERIFIED and item.points > 0  # 49 km sits inside the 30-120 km ramp
