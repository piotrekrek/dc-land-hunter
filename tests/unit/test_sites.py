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


def test_site_constants_match_the_scoring_config() -> None:
    """model/sites.py keeps two defaults for clustering; the scoring config is the source of truth."""
    from dclh_pipeline.config import load_scoring
    from dclh_pipeline.model.sites import DEFAULT_MW_PER_HA, SPLIT_SUGGESTION_HA

    cfg = load_scoring()
    assert cfg.target.mw_per_ha == DEFAULT_MW_PER_HA
    assert cfg.target.large_site_split_ha == SPLIT_SUGGESTION_HA


def test_oversized_cluster_is_cut_into_pieces_of_about_the_maximum() -> None:
    # A 1 km x 5 km strip of ten 10 ha parcels (100 ha each km², 500 ha total), all touching.
    strip = [Parcel(str(i), None, square(i * 500, 0, side=500)) for i in range(10)]
    whole = cluster_sites(strip)
    assert len(whole) == 1 and whole[0].area_ha == pytest.approx(250.0)
    pieces = cluster_sites(strip, max_site_ha=80.0)
    assert len(pieces) > 1
    assert sorted(pid for s in pieces for pid in s.parcel_ids) == sorted(str(i) for i in range(10))
    assert all("split_cluster" in s.flags for s in pieces)
    assert cluster_sites(strip, max_site_ha=80.0) == pieces  # deterministic


def test_cluster_under_the_maximum_is_left_alone() -> None:
    sites = cluster_sites([Parcel("1", None, square(0, 0, side=500))], max_site_ha=80.0)
    assert len(sites) == 1 and "split_cluster" not in sites[0].flags
