"""The pure parts of a parcel check: verdict, unknowns, resolving, the buffer and the sheet (no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import Point, box

from dclh_pipeline.check_sheet import LAYER_CAP_BYTES, build_sheet, layer_paths
from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import SiteLayers, build_site_features
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.parcel_check import (
    CHECK_MARGIN_M,
    FILTER_LABEL_PL,
    HARD_FILTER_ORDER,
    STEP_LABELS_PL,
    ResolvedParcel,
    check_buffer_m,
    next_steps,
    resolve_parcels,
    unknowns,
    verdict,
)
from dclh_pipeline.parcel_input import parse_inputs
from dclh_pipeline.scoring.engine import evaluate_hard_filters, score_site
from dclh_pipeline.scoring.run import ScoredSite
from dclh_pipeline.scoring.types import CategoryScore, Exclusion, GridBadge, ScoreItem, SiteScore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "live" / "check-spike-2026-10-24"
CFG = load_scoring()


def item(
    key: str, points: float, max_points: float, confidence: Confidence = Confidence.VERIFIED
) -> ScoreItem:
    return ScoreItem(
        key, "grid", key.upper(), points, max_points, max_points, confidence, None, None, f"opis {key}"
    )


def make_score(**over: object) -> SiteScore:
    base: dict[str, object] = dict(
        score=80.0,
        upside_score=85.0,
        completeness=0.9,
        excluded=False,
        exclusions=[],
        categories=[CategoryScore("grid", 40, 45, 45)],
        items=[item("line110", 27, 27), item("plan", 5, 25)],
        estimated_mw=75.0,
        large_campus=False,
        split_suggested=False,
        grid_badge=GridBadge.PROXY,
    )
    base.update(over)
    return SiteScore(**base)  # type: ignore[arg-type]


class TestVerdict:
    def test_a_hard_filter_gives_odpada_with_the_deciding_reason_first(self) -> None:
        score = make_score(
            excluded=True,
            exclusions=[
                Exclusion("forest", "las: 40% terenu", "penalty", 5),
                Exclusion("flood", "strefa zalewowa: 62% terenu", "exclude", 30),
            ],
        )
        result = verdict(score)
        assert result.kind == "odpada"
        assert result.text_pl == "Odpada: strefa zalewowa: 62% terenu."  # an exclusion, not the penalty

    def test_strong_and_complete_is_worth_checking_further(self) -> None:
        result = verdict(make_score(score=72.0, completeness=0.8))
        assert result.kind == "warto" and result.text_pl.startswith("Warto sprawdzać dalej")

    def test_low_completeness_asks_for_clarification_and_names_what_is_missing(self) -> None:
        score = make_score(
            score=75.0,
            completeness=0.4,
            items=[item("a", 0, 10, Confidence.MISSING), item("b", 5, 5)],
        )
        result = verdict(score)
        assert result.kind == "wymaga_wyjasnien"
        assert "A" in result.text_pl and "85" in result.text_pl

    def test_a_decent_but_not_strong_score_says_where_the_points_are_missing(self) -> None:
        score = make_score(
            score=67.0, completeness=0.95, items=[item("x", 1, 20), item("y", 9, 10), item("z", 3, 8)]
        )
        result = verdict(score)
        assert result.kind == "wymaga_wyjasnien"
        assert "wynik 67 pkt" in result.text_pl and "od 70" in result.text_pl
        assert "X; Z" in result.text_pl  # the two criteria that lose the most points, by what they lose
        assert "niski" not in result.text_pl  # 67 is not called low

    def test_the_strong_threshold_is_inclusive(self) -> None:
        assert verdict(make_score(score=70.0, completeness=0.7)).kind == "warto"
        assert verdict(make_score(score=69.9, completeness=0.7)).kind == "wymaga_wyjasnien"


def test_every_hard_filter_has_a_label_and_the_order_matches_the_scorer() -> None:
    parcel = Parcel("p", None, box(0, 0, 100, 100))
    site = cluster_sites([parcel], mw_per_ha=CFG.target.mw_per_ha, split_suggestion_ha=80)[0]
    features = build_site_features(site, SiteLayers(), CFG)
    _, known = evaluate_hard_filters(features, CFG)
    assert len(known) == len(HARD_FILTER_ORDER)
    assert set(HARD_FILTER_ORDER) == set(FILTER_LABEL_PL) and set(HARD_FILTER_ORDER) <= set(CFG.hard_filters)


class TestUnknowns:
    def scored(self) -> ScoredSite:
        site = cluster_sites(
            [Parcel("p", None, box(0, 0, 400, 400))], mw_per_ha=CFG.target.mw_per_ha, split_suggestion_ha=80
        )[0]
        features = build_site_features(site, SiteLayers(), CFG)  # no layer loaded at all
        return ScoredSite(site=site, features=features, score=score_site(features, CFG))

    def test_a_filter_that_could_not_be_evaluated_is_listed_never_read_as_fine(self) -> None:
        labels = [u.label_pl for u in unknowns(self.scored(), CFG, failed_steps=[])]
        assert "Nie sprawdzono: strefa zalewowa" in labels
        assert "Nie sprawdzono: gleby klas I–II" in labels  # the roman numerals keep their case
        assert "Nie sprawdzono: las" in labels
        assert "Nie sprawdzono: zabudowa mieszkaniowa" in labels
        assert "Nie sprawdzono: minimalna powierzchnia" not in labels  # the area is known

    def test_criteria_that_no_data_feeds_in_this_version_are_not_listed_as_things_to_find_out(self) -> None:
        labels = [u.label_pl for u in unknowns(self.scored(), CFG, failed_steps=[])]
        assert not {
            "Węzeł telekomunikacyjny w pobliżu",
            "Decyzja środowiskowa",
            "Sprzedający publiczny",
            "Planowana stacja odbiorcza PSE",
        } & set(labels)
        assert "Przeznaczenie terenu" in labels  # zoning can be found out: ask the gmina

    def test_the_reason_says_whether_the_download_failed_or_no_source_exists(self) -> None:
        by_label = {u.label_pl: u.why_pl for u in unknowns(self.scored(), CFG, failed_steps=["flood"])}
        assert "nie udało się pobrać" in by_label["Nie sprawdzono: strefa zalewowa"]
        assert "brak publicznego źródła" in by_label["Nie sprawdzono: rejestr zabytków"]

    def test_a_non_failed_missing_layer_is_not_blamed_on_a_download(self) -> None:
        by_label = {u.label_pl: u.why_pl for u in unknowns(self.scored(), CFG, failed_steps=[])}
        assert "brak publicznego źródła" in by_label["Nie sprawdzono: strefa zalewowa"]


def test_the_buffer_covers_every_distance_the_scorer_looks_at() -> None:
    needed = max(300.0, CFG.hard_filters["residential_within_m"].threshold_m or 0.0)
    assert check_buffer_m(CFG) >= needed
    assert check_buffer_m(CFG) >= CHECK_MARGIN_M


def test_every_step_has_a_polish_label() -> None:
    assert set(STEP_LABELS_PL) >= {
        "parcel",
        "neighbours",
        "flood",
        "mining",
        "bdot10k",
        "zoning",
        "kimpzp",
        "kiug",
        "scoring",
    }
    assert all(label and label[0].isupper() for label in STEP_LABELS_PL.values())


def parcel_for_steps() -> ResolvedParcel:
    return ResolvedParcel(
        "306201_1.0002.1573",
        "3062",
        "wielkopolskie",
        "powiat Konin",
        "Konin (miasto)",
        "0002",
        box(0, 0, 10, 10),
        0.01,
        18.0,
        52.0,
        True,
        "306201_1.0002.1573",
    )


def test_next_steps_carry_the_id_the_obreb_and_the_gmina_for_a_letter() -> None:
    steps = {s.key: s for s in next_steps(parcel_for_steps())}
    assert set(steps) == {"mpzp", "dso", "kw", "ooss"}
    copy = steps["mpzp"].copy_text or ""
    assert "306201_1.0002.1573" in copy and "obrębie 0002" in copy and "Konin (miasto)" in copy
    assert steps["kw"].copy_text is None


class FakeClient:
    """Answers ULDK from the saved live answers; `raise_for` makes a request fail like a server error."""

    def __init__(self, answers: dict[str, str], fail: str | None = None) -> None:
        self.answers, self.fail = answers, fail
        self.asked: list[dict[str, str]] = []

    def get(self, url: str, params: dict[str, str]) -> SimpleNamespace:
        self.asked.append(params)
        if self.fail:
            raise RuntimeError(self.fail)
        text = self.answers.get(params.get("id", params.get("xy", "")), "-1 brak wyników\n\n")
        return SimpleNamespace(text=text, raise_for_status=lambda: None)


def saved(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


ANSWERS = {
    "306201_1.0002.1573": saved("uldk_by_id_konin_1573.txt"),
    "142104_2.0013.562/2": saved("uldk_by_id_reguly_562_2.txt"),
}


class TestResolve:
    def test_a_parcel_id_becomes_a_parcel_with_geometry_names_and_a_powiat(self) -> None:
        result = resolve_parcels(FakeClient(ANSWERS), parse_inputs("306201_1.0002.1573"))  # type: ignore[arg-type]
        assert not result.issues and len(result.items) == 1
        parcel = result.items[0]
        assert parcel.parcel_id == "306201_1.0002.1573" and parcel.powiat_teryt == "3062"
        assert (
            parcel.obreb == "0002" and parcel.commune == "Konin (miasto)" and parcel.county == "powiat Konin"
        )
        assert parcel.area_ha > 0 and 50 < parcel.lat < 53 and 17 < parcel.lon < 20
        assert parcel.in_aoi is None  # no area of interest loaded: not labelled

    def test_an_unknown_id_is_an_issue_that_says_what_to_do(self) -> None:
        result = resolve_parcels(FakeClient(ANSWERS), parse_inputs("142104_2.0013.99999"))  # type: ignore[arg-type]
        assert not result.items
        assert "Nie znaleziono działki 142104_2.0013.99999" in result.issues[0].message_pl

    def test_the_same_parcel_given_twice_is_counted_once(self) -> None:
        text = "306201_1.0002.1573\n142104_2.0013.562/2"
        result = resolve_parcels(FakeClient(ANSWERS), parse_inputs(text))  # type: ignore[arg-type]
        assert [p.parcel_id for p in result.items] == ["306201_1.0002.1573", "142104_2.0013.562/2"]

    def test_a_server_failure_is_not_reported_as_a_missing_parcel(self) -> None:
        result = resolve_parcels(FakeClient(ANSWERS, fail="boom"), parse_inputs("306201_1.0002.1573"))  # type: ignore[arg-type]
        assert not result.items and "nie odpowiedział" in result.issues[0].message_pl

    def test_a_name_search_that_finds_nothing_says_to_add_the_obreb(self) -> None:
        result = resolve_parcels(FakeClient(ANSWERS), parse_inputs("Konin 0002 1573"))  # type: ignore[arg-type]
        assert "Dopisz obręb" in result.issues[0].message_pl

    def test_parse_problems_are_passed_on_with_the_lookups_that_worked(self) -> None:
        result = resolve_parcels(FakeClient(ANSWERS), parse_inputs("306201_1.0002.1573\n141/9"))  # type: ignore[arg-type]
        assert len(result.items) == 1 and len(result.issues) == 1

    def test_inside_and_outside_the_area_of_interest(self) -> None:
        inside = resolve_parcels(FakeClient(ANSWERS), parse_inputs("306201_1.0002.1573"), box(0, 0, 1e7, 1e7))  # type: ignore[arg-type]
        outside = resolve_parcels(FakeClient(ANSWERS), parse_inputs("306201_1.0002.1573"), box(0, 0, 10, 10))  # type: ignore[arg-type]
        assert inside.items[0].in_aoi is True and outside.items[0].in_aoi is False


class TestSheet:
    def test_paths_are_in_local_metres_north_up_around_the_parcel_centre(self) -> None:
        parcel = box(1000, 2000, 1100, 2100)
        sheet = build_sheet(
            parcel, [("flood", "Strefa zalewowa", "polygon", [box(1050, 2050, 1200, 2200)])], margin_m=500
        )
        assert sheet["origin_2180"] == [1050.0, 2050.0]
        assert sheet["bbox_m"] == [-550.0, -550.0, 550.0, 550.0]
        ring = sheet["parcel"][0]
        assert [-50.0, -50.0] in ring and [50.0, 50.0] in ring
        flood = sheet["layers"][0]
        assert flood["key"] == "flood" and flood["truncated"] is False
        assert [0.0, 0.0] in flood["paths"][0]  # clipped to the part that overlaps

    def test_a_layer_outside_the_sheet_is_empty_not_missing(self) -> None:
        sheet = build_sheet(
            box(0, 0, 10, 10), [("forest", "Las", "polygon", [box(9000, 9000, 9100, 9100)])], margin_m=100
        )
        assert sheet["layers"][0]["paths"] == [] and sheet["layers"][0]["truncated"] is False

    def test_a_polygon_with_a_hole_keeps_its_ring(self) -> None:
        donut = Point(0, 0).buffer(100).difference(Point(0, 0).buffer(40))
        paths, truncated = layer_paths([donut], (-200, -200, 200, 200), (0.0, 0.0))
        assert len(paths) == 2 and not truncated

    def test_a_layer_too_big_even_when_simplified_is_marked_truncated(self) -> None:
        many = [box(i * 3.0, 0, i * 3.0 + 2, 2) for i in range(20_000)]
        paths, truncated = layer_paths(many, (0, 0, 60_000, 10), (0.0, 0.0))
        assert truncated and paths == []

    def test_a_layer_is_simplified_to_fit_the_cap(self) -> None:
        wiggly = Point(0, 0).buffer(300, quad_segs=20_000)
        paths, truncated = layer_paths([wiggly], (-400, -400, 400, 400), (0.0, 0.0))
        assert not truncated and 0 < len(str(paths)) <= LAYER_CAP_BYTES * 2


@pytest.mark.parametrize("area_ha", [0.5, 12.0, 95.0])
def test_a_parcel_is_one_site_whatever_its_size(area_ha: float) -> None:
    from dclh_pipeline.parcel_check import _single_site

    side = (area_ha * 10_000) ** 0.5
    parcel = SimpleNamespace(parcel_id="p1", geom=box(0, 0, side, side))
    site = _single_site(parcel, CFG)  # type: ignore[arg-type]
    assert site.parcel_ids == ("p1",) and abs(site.area_ha - area_ha) < 1e-6
    assert "check" in site.flags and "split_cluster" not in site.flags  # a big parcel is kept whole
    assert site.needs_split_suggestion == (area_ha > CFG.target.large_site_split_ha)
