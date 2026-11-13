"""Which gminas have a plan ogólny in force: the nationwide act list of the Rejestr Urbanistyczny."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec, load_profiles
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.planning import (
    GMINA_PLAN_IN_FORCE,
    GMINA_PLAN_NONE,
    PlanRegister,
    build_plan_register,
    gmina_plan_fact,
)
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.profiles import Category, profile_verdict
from dclh_pipeline.scoring.types import ZoningKind
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.ru import RuActRegisterConnector
from tests.unit.test_scoring import F, full_features

ClientFactory = Callable[..., PoliteClient]
SPEC = SourceSpec(id="ru_acts", name="RU acts", used_for="t", plan="mvp", cadence="weekly")
LIVE = Path(__file__).parents[1] / "fixtures/live/rzepin-2026-11-11"
AS_OF = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
PROFILES = load_profiles()

ACT = """<wfs:member><app-pog:AktPlanowaniaPrzestrzennego gml:id="PL.ZIPPZP.1_{teryt}-POG_1POG_1">
<app-pog:idIIP><app-pog:Identyfikator><app-pog:przestrzenNazw>PL.ZIPPZP.1/{teryt}-POG</app-pog:przestrzenNazw>
<app-pog:lokalnyId>1POG</app-pog:lokalnyId><app-pog:wersjaId>1</app-pog:wersjaId></app-pog:Identyfikator>
</app-pog:idIIP><app-pog:tytul>Plan ogólny gminy {teryt}</app-pog:tytul>
<app-pog:obowiazujeOd>2026-09-22</app-pog:obowiazujeOd>
<app-pog:status xlink:href="http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/{status}"/>
<app-pog:zasiegPrzestrzenny><gml:Polygon><gml:exterior><gml:LinearRing>
<gml:posList>0 0 0 1 1 1 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</app-pog:zasiegPrzestrzenny></app-pog:AktPlanowaniaPrzestrzennego></wfs:member>"""


def act(teryt: str, status: str = "legalForce") -> str:
    return ACT.format(teryt=teryt, status=status)


def page(members: list[str], matched: int) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection numberMatched="{matched}" '
        f'numberReturned="{len(members)}" timeStamp="2026-11-13T09:00:00Z">{"".join(members)}'
        "</wfs:FeatureCollection>"
    ).encode()


def serve(members: list[str], *, matched: int | None = None, seen: list[dict[str, str]] | None = None):  # type: ignore[no-untyped-def]
    """A register that answers `startIndex` / `count` over `members`, as the live service does."""
    total = len(members) if matched is None else matched

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        params = dict(request.url.params)
        if seen is not None:
            seen.append(params)
        start, count = int(params["startIndex"]), int(params["count"])
        return httpx.Response(200, content=page(members[start : start + count], total))

    return handler


# --------------------------------------------------------------------------- the connector


def test_the_register_is_paged_nationwide_and_kept_without_geometry(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    members = [act(f"{i:06d}") for i in range(1, 24)] + [act("300101", "elaboration")]
    seen: list[dict[str, str]] = []
    connector = RuActRegisterConnector(SPEC)
    snapshot = connector.fetch(make_client(serve(members, seen=seen)), SnapshotStore(tmp_path))

    assert [p["startIndex"] for p in seen] == ["0", "10", "20"]
    assert all("bbox" not in p for p in seen)  # the whole country, not a search area
    assert b"posList" not in snapshot.path.read_bytes()
    table = connector.parse(snapshot)
    assert len(table) == 24
    assert table.iloc[0].to_dict() == {
        "teryt": "000001",
        "status": "legalForce",
        "title": "Plan ogólny gminy 000001",
        "in_force_from": "2026-09-22",
    }
    assert table.iloc[-1]["status"] == "elaboration"


def test_the_live_rzepin_act_gives_its_gmina(tmp_path: Path, make_client: ClientFactory) -> None:
    member = re.search(r"<wfs:member>.*</wfs:member>", (LIVE / "ru_pog_act.gml").read_text(), re.S)
    assert member is not None
    connector = RuActRegisterConnector(SPEC)
    snapshot = connector.fetch(make_client(serve([member.group(0)])), SnapshotStore(tmp_path))
    assert connector.parse(snapshot).iloc[0].to_dict() == {
        "teryt": "080504",
        "status": "legalForce",
        "title": "Plan ogólny gminy Rzepin",
        "in_force_from": "2026-09-11",
    }


def test_a_short_answer_fails_instead_of_calling_the_missing_gminas_planless(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    members = [act(f"{i:06d}") for i in range(1, 6)]
    with pytest.raises(ValueError, match="5 of 9"):
        RuActRegisterConnector(SPEC).fetch(make_client(serve(members, matched=9)), SnapshotStore(tmp_path))


def test_an_act_without_a_gmina_code_fails(tmp_path: Path, make_client: ClientFactory) -> None:
    broken = act("000001").replace("PL.ZIPPZP.1/000001-POG", "PL.ZIPPZP.1/plan")
    with pytest.raises(ValueError, match="gmina"):
        RuActRegisterConnector(SPEC).fetch(make_client(serve([broken])), SnapshotStore(tmp_path))


def test_an_empty_register_is_not_a_register(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot = store.save("ru_acts", json.dumps([]).encode(), suffix=".json")
    with pytest.raises(ValueError, match="no plan"):
        RuActRegisterConnector(SPEC).parse(snapshot)


# --------------------------------------------------------------------------- the fact


def register(*in_force: str) -> PlanRegister:
    return PlanRegister(AS_OF, frozenset(in_force))


def test_only_acts_in_force_count() -> None:
    import pandas as pd

    table = pd.DataFrame(
        {
            "teryt": ["080504", "300101"],
            "status": ["legalForce", "elaboration"],
            "title": "",
            "in_force_from": "",
        }
    )
    assert build_plan_register(table, AS_OF) == register("080504")


def test_a_gmina_with_a_plan_is_verified_and_one_without_is_estimated() -> None:
    reg = register("080504")
    # Both parts of a town-and-country gmina (…_4 town, …_5 country) share the plan.
    for parcel in ("080504_5.0010.85/8", "080504_4.0001.12"):
        yes = gmina_plan_fact(parcel, reg)
        assert yes is not None and (yes.value, yes.confidence) == (GMINA_PLAN_IN_FORCE, Confidence.VERIFIED)
        assert (yes.source_id, yes.as_of) == ("ru_acts", AS_OF)
    no = gmina_plan_fact("142105_2.0007.33", reg)
    # The register may lag behind a fresh resolution, so "none" is an estimate.
    assert no is not None and (no.value, no.confidence) == (GMINA_PLAN_NONE, Confidence.ESTIMATED)


@pytest.mark.parametrize("parcel", ["a1", "", "14210"])
def test_no_register_or_no_gmina_code_gives_no_fact(parcel: str) -> None:
    assert gmina_plan_fact("080504_5.0010.85/8", None) is None
    assert gmina_plan_fact(parcel, register("080504")) is None


# --------------------------------------------------------------------------- the verdict


def zoning_item(name: str, **overrides: object):  # type: ignore[no-untyped-def]
    v = profile_verdict(full_features(**overrides), name, PROFILES)
    return v, next(c for c in v.to_check if c.key == "zoning")


@pytest.mark.parametrize("name", ["built", "container"])
@pytest.mark.parametrize("zoning", [None, F(ZoningKind.UNKNOWN), F(ZoningKind.PROCEDURE_IN_PROGRESS)])
def test_no_plan_found_in_a_gmina_without_a_plan_ogolny_says_the_gmina_is_frozen(
    name: str, zoning: object
) -> None:
    v, item = zoning_item(name, zoning=zoning, gmina_plan_ogolny=F(GMINA_PLAN_NONE))
    assert v.category is Category.MANUAL and item.blocking  # unknown, not bad: the register may lag
    assert "nie ma planu ogólnego" in item.text_pl
    assert "WZ" in item.text_pl and "MPZP" in item.text_pl


def test_no_plan_found_in_a_gmina_with_a_plan_ogolny_asks_for_the_zone() -> None:
    v, item = zoning_item("built", zoning=F(ZoningKind.UNKNOWN), gmina_plan_ogolny=F(GMINA_PLAN_IN_FORCE))
    assert v.category is Category.MANUAL and item.blocking
    assert "Gmina ma plan ogólny" in item.text_pl and "stref" in item.text_pl


def test_without_the_register_the_old_wording_stays() -> None:
    _, item = zoning_item("built", zoning=F(ZoningKind.UNKNOWN))
    assert item.text_pl == "Brak planu miejscowego w rejestrze: sprawdź MPZP, plan ogólny i OUZ w gminie"


def test_an_mpzp_in_force_stays_green_in_a_gmina_without_a_plan_ogolny() -> None:
    v = profile_verdict(full_features(gmina_plan_ogolny=F(GMINA_PLAN_NONE)), "built", PROFILES)
    assert v.category is Category.GREEN
    assert "zoning" not in {c.key for c in v.to_check}


# --------------------------------------------------------------------------- a search stores the fact


def test_a_search_stores_the_gmina_fact_from_the_first_parcel() -> None:
    from shapely.geometry import box

    from dclh_pipeline.config import load_scoring
    from dclh_pipeline.model.features import SiteLayers, build_site_features
    from dclh_pipeline.model.sites import Parcel, cluster_sites

    cfg = load_scoring()
    site = cluster_sites(
        [Parcel("080504_5.0010.85/8", None, box(0, 0, 500, 500))],
        mw_per_ha=cfg.target.mw_per_ha,
        split_suggestion_ha=cfg.target.large_site_split_ha,
    )[0]
    with_plan = build_site_features(site, SiteLayers(plan_register=register("080504")), cfg)
    assert with_plan.gmina_plan_ogolny is not None
    assert with_plan.gmina_plan_ogolny.value == GMINA_PLAN_IN_FORCE
    without = build_site_features(site, SiteLayers(plan_register=register("221509")), cfg)
    assert without.gmina_plan_ogolny is not None and without.gmina_plan_ogolny.value == GMINA_PLAN_NONE
    assert build_site_features(site, SiteLayers(), cfg).gmina_plan_ogolny is None  # list never fetched


def refusing(members: list[str], broken: set[int]):  # type: ignore[no-untyped-def]
    """A register that answers 400 to any request whose window holds a broken act, as the live one does."""
    inner = serve(members)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        start, count = int(request.url.params["startIndex"]), int(request.url.params["count"])
        if broken & set(range(start, start + count)):
            return httpx.Response(400, content=b"Invalid request")
        return inner(request)

    return handler


def test_an_act_the_server_refuses_is_skipped_and_the_rest_of_its_page_is_kept(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    members = [act(f"{i:06d}") for i in range(1, 26)]  # 25 acts; the one at index 13 is broken
    connector = RuActRegisterConnector(SPEC)
    snapshot = connector.fetch(make_client(refusing(members, {13})), SnapshotStore(tmp_path))
    teryts = set(connector.parse(snapshot)["teryt"])
    assert teryts == {f"{i:06d}" for i in range(1, 26)} - {"000014"}


def test_a_refused_act_on_the_last_page_does_not_count_the_indexes_past_the_end(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    members = [act(f"{i:06d}") for i in range(1, 14)]  # 13 acts: the last page holds three
    connector = RuActRegisterConnector(SPEC)
    snapshot = connector.fetch(make_client(refusing(members, {11})), SnapshotStore(tmp_path))
    assert len(connector.parse(snapshot)) == 12


def test_too_many_refused_acts_fail_the_fetch(tmp_path: Path, make_client: ClientFactory) -> None:
    members = [act(f"{i:06d}") for i in range(1, 26)]
    with pytest.raises(ValueError, match="refused"):
        RuActRegisterConnector(SPEC).fetch(
            make_client(refusing(members, {10, 11, 12, 13, 14, 15})), SnapshotStore(tmp_path)
        )
