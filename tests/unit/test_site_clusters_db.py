"""Zoomed-out site bubbles: grouping on a tile-aligned grid (migration 0007), against a real PostGIS database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

from dclh_pipeline.db import session_scope

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

# Web Mercator tile (z=8, x=139, y=85) covers Bolesławiec and the country to its east. A z=8 cell is about
# 20 km wide in Mercator metres (about 12 km on the ground at this latitude), 8 x 8 of them per tile.
Z, X, Y = 8, 139, 85


@pytest.fixture(autouse=True)
def clean_sites() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(text("DELETE FROM sites"))

    wipe()
    yield
    wipe()


def add_site(site_id: str, lon: float, lat: float, *, score: float, excluded: bool = False) -> None:
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO sites (site_id, parcel_ids, area_ha, compactness, estimated_mw, score, upside_score, "
                "completeness, excluded, grid_badge, large_campus, split_suggested, detail, scored_at, geom, powiat) "
                "SELECT :id, ARRAY['p'], 12, 0.5, 72, :score, 0, 0.5, :excluded, 'proxy', false, false, '{}', now(), "
                "ST_Buffer(ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 2180), 150), '0201'"
            ),
            {"id": site_id, "lon": lon, "lat": lat, "score": score, "excluded": excluded},
        )


def cells(z: int, x: int, y: int) -> list[tuple[int, float | None]]:
    with session_scope() as session:
        rows = session.execute(
            text("SELECT n, best FROM tile_site_cluster_cells(:z, :x, :y) ORDER BY n DESC, best"),
            {"z": z, "x": x, "y": y},
        )
        return [(int(n), best) for n, best in rows]


def test_nearby_sites_share_a_bubble_and_far_ones_get_their_own() -> None:
    add_site("a", 15.570, 51.260, score=50)
    add_site("b", 15.575, 51.262, score=65)  # a few hundred metres from "a"
    add_site("c", 15.900, 51.100, score=30)  # about 25 km east, same tile, another cell
    assert cells(Z, X, Y) == [(2, 65.0), (1, 30.0)]


def test_the_bubble_shows_the_best_score_among_candidates_not_excluded_sites() -> None:
    add_site("a", 15.570, 51.260, score=40)
    add_site("b", 15.571, 51.261, score=95, excluded=True)
    assert cells(Z, X, Y) == [(2, 40.0)]  # counted, but a hard-filtered site cannot colour the bubble


def test_a_bubble_of_only_excluded_sites_has_no_score() -> None:
    add_site("a", 15.570, 51.260, score=80, excluded=True)
    assert cells(Z, X, Y) == [(1, None)]


@pytest.mark.parametrize("z", [0, 3, 6])
def test_every_site_lands_in_exactly_one_tile_of_a_zoom(z: int) -> None:
    # Zoom 0 and 3 tiles are far bigger than Poland: a bounding box in EPSG:2180 would drop every site there.
    add_site("a", 15.570, 51.260, score=50)
    add_site("b", 15.900, 51.100, score=50)
    add_site("c", 21.000, 52.200, score=50)  # Warsaw area: another tile at high zooms
    total = sum(n for x in range(2**z) for y in range(2**z) for n, _ in cells(z, x, y))
    assert total == 3


def test_a_tile_without_sites_is_an_empty_tile_not_an_error() -> None:
    add_site("a", 15.570, 51.260, score=50)
    with session_scope() as session:
        empty = session.execute(text("SELECT length(tiles.site_clusters(8, 0, 0))")).scalar_one()
        full = session.execute(
            text("SELECT length(tiles.site_clusters(:z, :x, :y))"), {"z": Z, "x": X, "y": Y}
        ).scalar_one()
    assert empty == 0 and full > 0
