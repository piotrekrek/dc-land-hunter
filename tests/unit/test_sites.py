from __future__ import annotations

import math

import pytest
from shapely.geometry import box

from dclh_pipeline.model.sites import Parcel, cluster_sites


def square(x: float, y: float, side: float = 100.0) -> object:
    return box(x, y, x + side, y + side)


def test_touching_parcels_form_one_site() -> None:
    sites = cluster_sites(
        [
            Parcel("1", "Z1", square(0, 0)),
            Parcel("2", "Z1", square(100, 0)),  # shares an edge with parcel 1
        ]
    )
    assert len(sites) == 1
    assert sites[0].parcel_ids == ("1", "2")
    assert sites[0].area_ha == pytest.approx(2.0)
    assert "multi_parcel" in sites[0].flags


def test_gap_within_tolerance_joins_and_beyond_does_not() -> None:
    near = cluster_sites([Parcel("1", "Z1", square(0, 0)), Parcel("2", "Z1", square(104, 0))])  # 4 m gap
    far = cluster_sites([Parcel("1", "Z1", square(0, 0)), Parcel("2", "Z1", square(106, 0))])  # 6 m gap
    assert len(near) == 1
    assert len(far) == 2


def test_different_zones_never_merge() -> None:
    sites = cluster_sites([Parcel("1", "Z1", square(0, 0)), Parcel("2", "Z2", square(100, 0))])
    assert len(sites) == 2
    assert {s.zone_id for s in sites} == {"Z1", "Z2"}


def test_unzoned_parcels_cluster_among_themselves() -> None:
    sites = cluster_sites([Parcel("1", None, square(0, 0)), Parcel("2", None, square(100, 0))])
    assert len(sites) == 1
    assert "no_zone" in sites[0].flags


def test_large_site_gets_split_suggestion_and_mw_estimate() -> None:
    big = cluster_sites([Parcel("1", "Z1", square(0, 0, side=1000))])[0]  # 100 ha
    assert big.area_ha == pytest.approx(100.0)
    assert big.needs_split_suggestion
    assert big.estimated_mw == pytest.approx(600.0)


def test_small_site_has_no_split_suggestion() -> None:
    site = cluster_sites([Parcel("1", "Z1", square(0, 0, side=300))])[0]  # 9 ha
    assert not site.needs_split_suggestion


def test_compactness_of_a_square() -> None:
    site = cluster_sites([Parcel("1", "Z1", square(0, 0))])[0]
    assert site.compactness == pytest.approx(math.pi / 4)  # 4·π·A / P² for a square


def test_site_id_is_stable_under_input_order() -> None:
    a = [Parcel("1", "Z1", square(0, 0)), Parcel("2", "Z1", square(100, 0))]
    b = list(reversed(a))
    assert cluster_sites(a)[0].site_id == cluster_sites(b)[0].site_id


def test_site_id_changes_with_zone() -> None:
    one = cluster_sites([Parcel("1", "Z1", square(0, 0))])[0]
    two = cluster_sites([Parcel("1", "Z2", square(0, 0))])[0]
    assert one.site_id != two.site_id
