from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from dclh_pipeline.model.aoi import (
    DROP_REFERENCE_LONLAT,
    KEEP_REFERENCE_LONLAT,
    build_aoi,
    extend_line,
    lonlat_to_2180,
)

# A synthetic "country": 1000 x 1000 km square; the "river" runs south to north at x=600 km, but stops
# 100 km short of both edges, as the real Vistula stops short of the border and the Baltic coast.
COUNTRY = Polygon([(0, 0), (1_000_000, 0), (1_000_000, 1_000_000), (0, 1_000_000)])
RIVER = LineString([(600_000, 100_000), (600_000, 500_000), (600_000, 900_000)])
WEST = Point(300_000, 500_000)
EAST = Point(800_000, 500_000)


# A "city" on the excluded (east) side, like Toruń on the south bank, and a neighbour that is not in the include list,
# like Praga.
CITY = box(700_000, 480_000, 720_000, 520_000)
CITY_POINT = Point(710_000, 500_000)
PRAGA = box(750_000, 480_000, 760_000, 520_000)
PRAGA_POINT = Point(755_000, 500_000)


def test_extend_line_extends_both_ends() -> None:
    ext = extend_line(RIVER, 50_000)
    assert ext.coords[0] == (600_000, 50_000)
    assert ext.coords[-1] == (600_000, 950_000)
    assert ext.length == pytest.approx(RIVER.length + 100_000)


def test_unextended_river_does_not_split() -> None:
    from shapely.ops import split

    assert len(split(COUNTRY, RIVER).geoms) == 1  # why extension is needed


def test_build_aoi_keeps_the_west_side() -> None:
    aoi = build_aoi(COUNTRY, RIVER, WEST, EAST, extension_m=200_000)
    assert aoi.contains(WEST) and not aoi.contains(EAST)
    assert aoi.area == pytest.approx(600_000 * 1_000_000)


def test_included_unit_on_the_excluded_side_is_added_whole() -> None:
    aoi = build_aoi(COUNTRY, RIVER, WEST, EAST, include=[CITY])
    assert aoi.contains(CITY_POINT)
    assert aoi.contains(WEST) and not aoi.contains(EAST)
    assert aoi.area == pytest.approx(600_000 * 1_000_000 + CITY.area)


def test_units_not_in_the_include_list_stay_out() -> None:
    aoi = build_aoi(COUNTRY, RIVER, WEST, EAST, include=[CITY])
    assert not aoi.contains(PRAGA_POINT)
    assert not build_aoi(COUNTRY, RIVER, WEST, EAST).contains(CITY_POINT)


def test_reversed_line_direction_gives_same_result() -> None:
    reversed_river = LineString(list(RIVER.coords)[::-1])
    a = build_aoi(COUNTRY, RIVER, WEST, EAST)
    b = build_aoi(COUNTRY, reversed_river, WEST, EAST)
    assert a.equals(b)


def test_same_side_reference_points_are_rejected() -> None:
    with pytest.raises(ValueError, match="same piece"):
        build_aoi(COUNTRY, RIVER, WEST, Point(100_000, 500_000))


def test_river_that_misses_the_country_is_rejected() -> None:
    far = LineString([(2_000_000, 0), (2_000_000, 100_000)])
    with pytest.raises(ValueError, match="does not divide"):
        build_aoi(COUNTRY, far, WEST, EAST, extension_m=1_000)


def test_reference_points_are_in_poland_in_epsg_2180() -> None:
    # EPSG:2180 covers Poland roughly x 140k-860k, y 120k-800k.
    for lon, lat in (KEEP_REFERENCE_LONLAT, DROP_REFERENCE_LONLAT):
        p = lonlat_to_2180(lon, lat)
        assert 140_000 < p.x < 860_000 and 120_000 < p.y < 800_000
    assert lonlat_to_2180(*KEEP_REFERENCE_LONLAT).x < lonlat_to_2180(*DROP_REFERENCE_LONLAT).x
