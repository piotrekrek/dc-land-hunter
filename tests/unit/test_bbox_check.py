"""The axis-order check of the WFS connectors accepts loose matches and rejects swapped axes."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from dclh_pipeline.sources.bbox_check import NEAR_M, features_near_bbox

BBOX = (322_000.0, 345_000.0, 323_000.0, 346_000.0)  # a parcel-sized box near Żarów (easting, northing)


def table(*geoms: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=list(geoms), crs="EPSG:2180")  # type: ignore[arg-type]


def test_a_feature_inside_the_box_is_near() -> None:
    assert features_near_bbox(table(box(322_100, 345_100, 322_200, 345_200)), BBOX)


def test_a_feature_a_few_hundred_metres_outside_is_still_near() -> None:
    # The real answer of the plan ogólny service for a parcel check: a zone 213 m north of the box.
    assert features_near_bbox(table(box(314_837, 346_213, 323_060, 350_905)), BBOX)


def test_an_empty_answer_is_a_real_empty_layer_not_an_error() -> None:
    assert features_near_bbox(table(), BBOX)


def test_swapped_axes_put_the_feature_far_away_and_are_caught() -> None:
    swapped = box(345_100, 322_100, 345_200, 322_200)  # (northing, easting) instead of (easting, northing)
    assert not features_near_bbox(table(swapped), BBOX)


def test_one_near_feature_is_enough() -> None:
    assert features_near_bbox(
        table(box(900_000, 900_000, 900_100, 900_100), box(322_100, 345_100, 322_200, 345_200)), BBOX
    )


def test_the_tolerance_is_a_few_kilometres_at_most() -> None:
    assert 500 <= NEAR_M <= 5_000
    just_outside = box(323_000 + NEAR_M + 50, 345_100, 323_000 + NEAR_M + 100, 345_200)
    assert not features_near_bbox(table(just_outside), BBOX)
