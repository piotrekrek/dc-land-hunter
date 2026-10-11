"""One scoring run over synthetic candidates. Coordinates are metres (EPSG:2180) near Warsaw."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from enum import StrEnum

from shapely.geometry import LineString, Point, box

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import Line, SiteLayers, Substation, ZoningLayer, build_grid_index
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.scoring.run import score_candidates, to_jsonable
from dclh_pipeline.scoring.types import ZoningKind

CFG = load_scoring()
X0, Y0 = 640_000.0, 490_000.0


def parcel_sites() -> list:
    parcels = [
        Parcel("a1", "z-a", box(X0, Y0, X0 + 800, Y0 + 400)),  # 32 ha, next to the grid
        Parcel("b1", "z-b", box(X0 + 2_000, Y0, X0 + 2_200, Y0 + 200)),  # 4 ha: below the minimum
        Parcel("c1", "z-c", box(X0 + 5_000, Y0, X0 + 5_600, Y0 + 250)),  # 15 ha, far from the grid
    ]
    return cluster_sites(parcels)


def layers() -> SiteLayers:
    grid = build_grid_index(
        [Substation(1, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(X0 + 900, Y0 + 200))],
        [Line(10, 110_000, LineString([(X0 + 1_000, Y0 - 5_000), (X0 + 1_000, Y0 + 5_000)]))],
    )
    zoning = ZoningLayer(
        "ru", None, ((ZoningKind.PLAN_OGOLNY_SG, box(X0 - 50, Y0 - 50, X0 + 900, Y0 + 500)),)
    )
    return SiteLayers(grid=grid, zoning=zoning)


def test_scores_every_site_and_keeps_excluded_ones_sorted_by_id() -> None:
    scored = score_candidates(parcel_sites(), layers(), CFG)
    assert [s.site.site_id for s in scored] == sorted(s.site.site_id for s in scored)
    assert len(scored) == 3
    excluded = [s for s in scored if s.score.excluded]
    assert len(excluded) == 1 and excluded[0].site.area_ha < 10


def test_site_near_grid_outranks_distant_site() -> None:
    scored = {s.site.zone_id: s.score.score for s in score_candidates(parcel_sites(), layers(), CFG)}
    assert scored["z-a"] > scored["z-c"]


def test_features_carry_provenance() -> None:
    scored = {s.site.zone_id: s for s in score_candidates(parcel_sites(), layers(), CFG)}
    features = scored["z-a"].features
    assert features.area_ha is not None and features.area_ha.source_id == "egib"
    assert features.dist_gpz110_km is not None and features.dist_gpz110_km.source_id == "osm_power"
    assert features.zoning is not None and features.zoning.value is ZoningKind.PLAN_OGOLNY_SG


def test_to_jsonable_uses_enum_values_and_iso_dates() -> None:
    class Colour(StrEnum):
        RED = "red"

    @dataclasses.dataclass(frozen=True)
    class Thing:
        when: datetime
        colour: Colour
        parts: tuple[int, ...]

    out = to_jsonable(Thing(datetime(2026, 10, 10, 12, tzinfo=UTC), Colour.RED, (1, 2)))
    assert out == {"when": "2026-10-10T12:00:00+00:00", "colour": "red", "parts": [1, 2]}
