"""Contract tests on real samples (tests/fixtures/live). Each test states one thing the code relies on.

Conventions:
- A known gap is `xfail(strict=True)`: it stays green while the gap is open, and the run fails as soon as the
  gap is fixed, so the marker gets removed.
- A decision the owner still has to make is `skip` with the decision in the reason.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import Point, box

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.scoring.run import score_candidates
from dclh_pipeline.sources.egib import read_page
from dclh_pipeline.sources.prg import locate
from dclh_pipeline.sources.uldk import parse_point
from tests.conftest import FIXTURES

KONIN_EGIB = FIXTURES / "live" / "cge-konin" / "egib_getfeature.gml"
KONIN_ULDK = FIXTURES / "live" / "cge-konin" / "uldk_getparcelbyxy.txt"
CFG = load_scoring()


@pytest.fixture(scope="module")
def konin_parcels():  # type: ignore[no-untyped-def]
    return read_page(KONIN_EGIB)


def as_parcels(frame) -> list[Parcel]:  # type: ignore[no-untyped-def]
    return [Parcel(str(r["ID_DZIALKI"]), str(r["NAZWA_GMINY"]), r.geometry) for _, r in frame.iterrows()]


# --- EGiB: what the parser relies on -------------------------------------------------------------------------


def test_egib_geometries_are_valid_polygons(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    assert konin_parcels.geometry.is_valid.all()
    assert set(konin_parcels.geom_type) == {"Polygon"}


def test_egib_parcel_areas_are_plausible(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    ha = konin_parcels.geometry.area / 10_000
    assert ha.min() > 0.001 and ha.max() < 50, (ha.min(), ha.max())


@pytest.mark.skip(
    reason="DECISION (owner): a parcel that comes back as two features with one id. Keep all parts or merge?"
)
def test_multi_part_parcel_keeps_all_its_area() -> None:
    pass


# --- ULDK -------------------------------------------------------------------------------------------------


def test_uldk_point_gives_the_teryt_id_and_commune() -> None:
    parcel = parse_point(KONIN_ULDK.read_text(encoding="utf-8"))
    assert parcel.parcel_id.startswith("306201_1.")
    assert parcel.commune == "Konin (miasto)"


def test_uldk_error_status_never_becomes_a_parcel() -> None:
    with pytest.raises(ValueError):
        parse_point("-1\nbrak wyników\n")


# --- Sites: real parcels through clustering and scoring -------------------------------------------------------


def test_every_real_parcel_is_in_exactly_one_site(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    sites = cluster_sites(as_parcels(konin_parcels))
    members = [pid for site in sites for pid in site.parcel_ids]
    assert sorted(members) == sorted(konin_parcels["ID_DZIALKI"])


def test_real_konin_sites_score_within_bounds_and_keep_provenance(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    scored = score_candidates(cluster_sites(as_parcels(konin_parcels)), _no_layers(), CFG)
    assert scored
    for item in scored:
        assert 0 <= item.score.score <= 100
        assert 0 <= item.score.completeness <= 1
        assert item.features.area_ha is not None
        assert item.features.area_ha.source_id == "egib"
        assert item.score.estimated_mw == pytest.approx(item.site.area_ha * CFG.target.mw_per_ha)


def test_real_konin_sites_below_minimum_area_are_excluded(konin_parcels) -> None:  # type: ignore[no-untyped-def]
    scored = score_candidates(cluster_sites(as_parcels(konin_parcels)), _no_layers(), CFG)
    for item in scored:
        if item.site.area_ha < CFG.target.ha_min:
            assert item.score.excluded, item.site.site_id


@pytest.mark.xfail(
    strict=True, reason="Known gap: the grid, zoning and constraint layers are not loaded for Konin yet"
)
def test_konin_sites_have_grid_features() -> None:
    scored = score_candidates(cluster_sites(as_parcels(read_page(KONIN_EGIB))), _no_layers(), CFG)
    assert all(item.features.dist_gpz110_km is not None for item in scored)


@given(
    st.lists(
        st.tuples(st.integers(0, 40), st.integers(0, 40)),
        min_size=1,
        max_size=25,
        unique=True,
    )
)
@settings(max_examples=60, deadline=None)
def test_clustering_partitions_any_grid_of_parcels(cells: list[tuple[int, int]]) -> None:
    """Random block layouts: every parcel lands in exactly one site, whatever the touching pattern."""
    parcels = [
        Parcel(f"p{i}", "z", box(x * 100, y * 100, x * 100 + 90, y * 100 + 90))
        for i, (x, y) in enumerate(cells)
    ]
    sites = cluster_sites(parcels)
    members = sorted(pid for site in sites for pid in site.parcel_ids)
    assert members == sorted(p.parcel_id for p in parcels)


# --- Golden set and open questions ----------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="Known gap: CGE Konin has no point yet (the plot is not public; owner to confirm)"
)
def test_golden_konin_has_coordinates() -> None:
    import yaml

    golden = yaml.safe_load(
        (Path(__file__).parents[2] / "tests" / "golden" / "sites.yaml").read_text(encoding="utf-8")
    )
    konin = next(s for s in golden["sites"] if s["name"] == "CGE Konin")
    assert konin["lon"] is not None and konin["lat"] is not None


PRG_KONIN = FIXTURES / "prg" / "konin_region.geojson"


@pytest.fixture(scope="module")
def prg_konin():  # type: ignore[no-untyped-def]
    return gpd.read_file(PRG_KONIN)


def test_konin_point_is_in_the_konin_gmina(prg_konin) -> None:  # type: ignore[no-untyped-def]
    """The ULDK point for Konin and the PRG boundaries must agree on the commune (TERYT 3062011)."""
    found = locate(prg_konin, Point(448894.6, 485814.39))
    assert found["gmina"]["teryt"] == "3062011"
    assert found["powiat"]["teryt"] == "3062"
    assert found["voivodeship"]["name"] == "wielkopolskie"


def test_every_konin_parcel_is_in_the_konin_gmina_by_boundaries(konin_parcels, prg_konin) -> None:  # type: ignore[no-untyped-def]
    """The commune comes from the boundaries: the EGiB 'NAZWA_GMINY' attribute is not used."""
    communes = {locate(prg_konin, geom).get("gmina", {}).get("teryt") for geom in konin_parcels.geometry}
    assert communes == {"3062011"}


@pytest.mark.skip(reason="Needs the RU connector, which is not written yet (plan step 3)")
def test_ru_zone_codes_map_to_zoning_kinds() -> None:
    pass


def _no_layers():  # type: ignore[no-untyped-def]
    from dclh_pipeline.model.features import SiteLayers

    return SiteLayers()
