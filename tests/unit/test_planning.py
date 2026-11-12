"""Planning path after the plan ogólny reform: OUZ, planning_path, zone parameters, score and flags."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import box

from dclh_pipeline.config import SourceSpec, load_profiles, load_scoring
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.planning import (
    PlanOgolnyLayer,
    build_plan_layer,
    planning_facts,
    resolution_number,
)
from dclh_pipeline.profiles import profile_verdict
from dclh_pipeline.scoring import score_site
from dclh_pipeline.scoring.types import (
    PATH_MPZP_IN_FORCE,
    PATH_MPZP_OR_ZPI,
    PATH_WZ_POSSIBLE,
    ZoningKind,
)
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.ru import RuOuzConnector, RuPlanActConnector, RuPlanOgolnyConnector
from tests.unit.test_scoring import F, full_features

ClientFactory = Callable[..., PoliteClient]
SPEC = SourceSpec(id="ru", name="RU", used_for="t", plan="mvp", cadence="weekly")
LIVE = Path(__file__).parents[1] / "fixtures/live/rzepin-2026-11-11"
# Around Rzepin (easting, northing in EPSG:2180); the live samples were taken from this area on 11.11.2026.
BBOX = (210_000.0, 504_000.0, 220_000.0, 516_000.0)
IN_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"
DRAFT = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/elaboration"
RESOLUTION = "https://x/DokumentFormalny/PL.ZIPPZP.10434/080504-POG/POG_Rzepin_XXXIV_224_2026_uchwalajaca"
SCORING = load_scoring()
PROFILES = load_profiles()


def fetch_table(connector_cls: type, path: Path, tmp_path: Path, make_client: ClientFactory):  # type: ignore[no-untyped-def]
    body = path.read_bytes()
    n = len(re.findall(rb"<wfs:member>", body))
    body = re.sub(rb'numberMatched="\d+"', f'numberMatched="{n}"'.encode(), body)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    connector = connector_cls(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(handler), SnapshotStore(tmp_path))
    return connector.parse(snapshot)


# --------------------------------------------------------------------------- the connector on live samples


def test_zone_parameters_parse_from_the_live_rzepin_strefa_gospodarcza(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    table = fetch_table(RuPlanOgolnyConnector, LIVE / "ru_pog_zone_SP.gml", tmp_path, make_client)
    row = table.iloc[0]
    assert row["symbol"] == "SP"
    assert row["zone_href"].endswith("strefaGospodarcza")
    assert (row["max_height_m"], row["max_build_up_pct"], row["min_pbc_pct"]) == ("20.0", "70.0", "20.0")


def test_a_zone_without_limits_has_empty_parameters_not_a_failure(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    body = (LIVE / "ru_pog_zone_SP.gml").read_text()
    body = re.sub(r"<app-pog:(maks|min)\w+[^>]*>[^<]*</app-pog:\w+>", "", body)
    (tmp_path / "bare.gml").write_text(body)
    table = fetch_table(RuPlanOgolnyConnector, tmp_path / "bare.gml", tmp_path, make_client)
    assert table.iloc[0]["max_height_m"] == ""


def test_ouz_and_plan_act_parse_from_live_samples(tmp_path: Path, make_client: ClientFactory) -> None:
    ouz = fetch_table(RuOuzConnector, LIVE / "ru_pog_ouz.gml", tmp_path, make_client)
    assert len(ouz) == 3 and ouz["status_href"].str.endswith("legalForce").all()
    act = fetch_table(RuPlanActConnector, LIVE / "ru_pog_act.gml", tmp_path, make_client).iloc[0]
    assert act["title"] == "Plan ogólny gminy Rzepin"
    assert act["in_force_from"] == "2026-09-11"
    assert resolution_number(act["resolution_href"]) == "XXXIV/224/2026"


def test_resolution_number() -> None:
    assert resolution_number(RESOLUTION) == "XXXIV/224/2026"
    assert resolution_number("https://x/doc/whatever") is None
    assert resolution_number(None) is None


# --------------------------------------------------------------------------- planning_path classification


def tables(
    *,
    ouz: list[object] | None = None,
    mpzp: list[object] | None = None,
    zone_params: tuple[str, str, str] = ("20.0", "70.0", "20.0"),
) -> PlanOgolnyLayer:
    """A plan ogólny act over 0..1000 m with one zone over it; OUZ and MPZP outlines as given."""
    cols = ["plan_id", "status_href"]

    def frame(geoms: list[object], **extra: list[str]) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"plan_id": ["p"] * len(geoms), "status_href": [IN_FORCE] * len(geoms), **extra},
            geometry=geoms,
            crs="EPSG:2180",
        )

    zones = frame(
        [box(0, 0, 1000, 1000)],
        max_height_m=[zone_params[0]],
        max_build_up_pct=[zone_params[1]],
        min_pbc_pct=[zone_params[2]],
        max_intensity=["nan"],
    )
    acts = frame(
        [box(0, 0, 1000, 1000)],
        title=["Plan ogólny gminy Rzepin"],
        in_force_from=["2026-09-11"],
        resolution_href=[RESOLUTION],
    )
    assert cols
    return build_plan_layer(zones, frame(ouz or []), acts, frame(mpzp or []), None)


SITE = box(100, 100, 200, 200)


def path_of(layer: PlanOgolnyLayer | None, site=SITE, *, kimpzp: bool = False) -> str | None:  # type: ignore[no-untyped-def]
    facts = planning_facts(site, layer, mpzp_from_kimpzp=kimpzp)
    return facts["planning_path"].value if "planning_path" in facts else None


def test_outside_the_ouz_without_an_mpzp_the_path_is_mpzp_or_zpi() -> None:
    layer = tables(ouz=[box(800, 800, 900, 900)])
    facts = planning_facts(SITE, layer, mpzp_from_kimpzp=False)
    assert facts["in_ouz"].value == "no"
    assert facts["planning_path"].value == PATH_MPZP_OR_ZPI
    assert facts["plan_ogolny_act"].value == "XXXIV/224/2026"
    assert facts["plan_ogolny_from"].value == "2026-09-11"
    assert facts["zone_max_height_m"].value == 20.0
    assert facts["zone_max_build_up_pct"].value == 70.0
    assert facts["zone_min_pbc_pct"].value == 20.0
    assert "zone_max_intensity" not in facts  # "nan" in the register: not set
    assert facts["in_ouz"].source_id == "ru"


def test_inside_the_ouz_without_an_mpzp_a_wz_is_possible() -> None:
    layer = tables(ouz=[box(50, 50, 300, 300)])
    assert planning_facts(SITE, layer, mpzp_from_kimpzp=False)["in_ouz"].value == "yes"
    assert path_of(layer) == PATH_WZ_POSSIBLE


def test_a_site_partly_in_the_ouz_still_needs_an_mpzp_or_zpi() -> None:
    layer = tables(ouz=[box(0, 0, 150, 1000)])  # half of the site
    assert planning_facts(SITE, layer, mpzp_from_kimpzp=False)["in_ouz"].value == "partial"
    assert path_of(layer) == PATH_MPZP_OR_ZPI


def test_an_mpzp_in_force_wins_inside_and_outside_the_ouz() -> None:
    assert path_of(tables(ouz=[]), kimpzp=True) == PATH_MPZP_IN_FORCE  # KIMPZP answered
    assert path_of(tables(ouz=[box(50, 50, 300, 300)], mpzp=[box(0, 0, 500, 500)])) == PATH_MPZP_IN_FORCE


def test_no_plan_ogolny_in_the_register_gives_no_facts() -> None:
    assert planning_facts(SITE, None, mpzp_from_kimpzp=False) == {}
    far = tables()
    assert planning_facts(box(5000, 5000, 5100, 5100), far, mpzp_from_kimpzp=False) == {}  # another gmina


def test_a_draft_plan_ogolny_and_draft_ouz_do_not_count() -> None:
    layer = tables(ouz=[box(50, 50, 300, 300)])
    draft_acts = layer.acts  # acts are filtered by status when built
    assert draft_acts
    zones = gpd.GeoDataFrame(
        {
            "status_href": [DRAFT],
            "max_height_m": ["9"],
            "max_build_up_pct": ["1"],
            "min_pbc_pct": ["1"],
            "max_intensity": ["1"],
        },
        geometry=[box(0, 0, 1000, 1000)],
    )
    acts = gpd.GeoDataFrame(
        {"status_href": [DRAFT], "title": ["x"], "in_force_from": [""], "resolution_href": [""]},
        geometry=[box(0, 0, 1000, 1000)],
    )
    empty = gpd.GeoDataFrame({"status_href": []}, geometry=[])
    layer = build_plan_layer(zones, empty, acts, empty, None)
    assert planning_facts(SITE, layer, mpzp_from_kimpzp=False) == {}


# --------------------------------------------------------------------------- score and flags


def sg(path: str | None, **extra: object):  # type: ignore[no-untyped-def]
    return full_features(
        zoning=F(ZoningKind.PLAN_OGOLNY_SG),
        **({"planning_path": F(path), "in_ouz": F("no")} if path else {}),
        **extra,
    )


def zoning_points(f) -> float:  # type: ignore[no-untyped-def]
    return next(i for i in score_site(f, SCORING).items if i.key == "zoning").points


def test_plan_ogolny_zone_outside_the_ouz_scores_60_between_sg_and_procedure() -> None:
    sg_inside = zoning_points(sg(PATH_WZ_POSSIBLE))
    sg_unknown_ouz = zoning_points(sg(None))
    outside = zoning_points(sg(PATH_MPZP_OR_ZPI))
    procedure = zoning_points(full_features(zoning=F(ZoningKind.PROCEDURE_IN_PROGRESS)))
    assert sg_inside == pytest.approx(sg_unknown_ouz) == pytest.approx(20.0)  # 80% of 25
    assert outside == pytest.approx(15.0)  # 60% of 25
    assert procedure < outside < sg_inside


def test_an_mpzp_in_force_with_an_allowing_zone_stays_100() -> None:
    f = full_features(zoning=F(ZoningKind.MPZP_PRODUCTION), planning_path=F(PATH_MPZP_IN_FORCE))
    assert zoning_points(f) == pytest.approx(25.0)


def test_the_score_explains_why_outside_the_ouz_is_lower() -> None:
    zone = next(i for i in score_site(sg(PATH_MPZP_OR_ZPI), SCORING).items if i.key == "zoning")
    assert "MPZP lub ZPI" in zone.explanation_pl and "60%" in zone.explanation_pl


def keys(f, name: str = "built") -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {c.key: c for c in profile_verdict(f, name, PROFILES).to_check}


def test_height_limit_of_20_m_or_less_raises_the_chimney_flag() -> None:
    flagged = keys(full_features(zone_max_height_m=F(20.0)))["zone_height"]
    assert flagged.text_pl == "Sprawdź kominy agregatów i chłodzenie względem limitu wysokości (20 m)."  # type: ignore[attr-defined]
    assert "zone_height" not in keys(full_features(zone_max_height_m=F(25.0)))
    assert "zone_height" not in keys(full_features())  # no parameter: nothing to flag


def test_planning_path_and_plan_validity_are_listed_to_check() -> None:
    f = sg(PATH_MPZP_OR_ZPI, plan_ogolny_act=F("XXXIV/224/2026"), plan_ogolny_from=F("2026-09-11"))
    checks = keys(f)
    assert "MPZP lub ZPI" in checks["planning_path"].text_pl  # type: ignore[attr-defined]
    assert "XXXIV/224/2026" in checks["plan_validity"].text_pl  # type: ignore[attr-defined]
    assert "dzienniku urzędowym" in checks["plan_validity"].text_pl  # type: ignore[attr-defined]
    assert "ouz" not in checks  # the register answered, so the OUZ question is gone


def test_without_register_data_the_ouz_question_stays_and_nothing_is_bad() -> None:
    checks = keys(full_features(zoning=F(ZoningKind.PLAN_OGOLNY_SG)))
    assert "ouz" in checks
    assert "planning_path" not in checks and "plan_validity" not in checks
