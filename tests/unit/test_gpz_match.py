"""GPZ name matching (plan Phase 3, R5): normalisation, fuzzy matches, ambiguity and manual overrides."""

from __future__ import annotations

import pytest
from shapely.geometry import Point

from dclh_pipeline.model.features import Substation
from dclh_pipeline.model.gpz_match import (
    GpzCandidate,
    gpz_candidates,
    match_gpz_names,
    match_summary,
    normalise_name,
)
from dclh_pipeline.model.grid import Dso, SubstationClass


def _cand(key: int, name: str) -> GpzCandidate:
    return GpzCandidate(key, name, Point(1000.0 * key, 0.0))


def test_normalise_folds_diacritics_case_and_generic_words() -> None:
    assert normalise_name("GPZ Piaski-Północ") == "piaski polnoc"
    assert normalise_name("Stacja Ząbki") == "zabki"
    assert normalise_name("Łódź Wschód") == "lodz wschod"
    assert normalise_name("  ") == ""
    assert normalise_name("GPZ") == ""  # a bare prefix names nothing


def test_exact_name_after_normalisation_is_a_fuzzy_match_with_score_100() -> None:
    candidates = [_cand(1, "GPZ Piaski Północ"), _cand(2, "GPZ Żabno")]
    (result,) = match_gpz_names("enea", ["Piaski Polnoc"], candidates, min_score=88)
    assert result.candidate_key == 1
    assert result.method == "fuzzy"
    assert result.score == 100


def test_small_typo_still_matches_above_threshold() -> None:
    candidates = [_cand(1, "Piaski Północne"), _cand(2, "Żabno")]
    (result,) = match_gpz_names("energa", ["Pizaski Polnocne"], candidates, min_score=88)
    assert result.candidate_key == 1
    assert 88 <= result.score < 100


def test_below_threshold_stays_unmatched_with_the_score_as_reason() -> None:
    candidates = [_cand(1, "Piaski"), _cand(2, "Żabno")]
    (result,) = match_gpz_names("tauron", ["Kraków Płaszów"], candidates, min_score=88)
    assert result.candidate_key is None
    assert result.method == "none"
    assert "najlepsza zgodność" in result.reason


def test_two_stations_with_the_same_name_make_the_match_ambiguous() -> None:
    candidates = [_cand(1, "GPZ Piaski"), _cand(2, "Stacja Piaski")]
    (result,) = match_gpz_names("enea", ["Piaski"], candidates, min_score=88)
    assert result.candidate_key is None
    assert "niejednoznaczne" in result.reason


def test_manual_mapping_wins_over_fuzzy_matching() -> None:
    candidates = [_cand(1, "Piaski"), _cand(2, "Żabno")]
    (result,) = match_gpz_names(
        "enea", ["Zabno Stare"], candidates, manual={normalise_name("Zabno Stare"): 2}, min_score=88
    )
    assert result.candidate_key == 2
    assert result.method == "manual"
    assert result.score == 100


def test_manual_mapping_to_an_unknown_station_fails_loudly() -> None:
    candidates = [_cand(1, "Piaski")]
    with pytest.raises(ValueError, match="not a known substation"):
        match_gpz_names("enea", ["Piaski"], candidates, manual={normalise_name("Piaski"): 99})


def test_blank_name_is_unmatched_and_empty_candidate_list_matches_nothing() -> None:
    (blank,) = match_gpz_names("enea", ["GPZ"], [_cand(1, "Piaski")], min_score=88)
    assert blank.candidate_key is None
    assert blank.reason == "brak nazwy do dopasowania"
    (none_found,) = match_gpz_names("enea", ["Piaski"], [], min_score=88)
    assert none_found.candidate_key is None


def test_match_summary_counts_methods_and_rate() -> None:
    results = match_gpz_names(
        "enea",
        ["Piaski", "Żabno", "Kraków Płaszów"],
        [_cand(1, "Piaski"), _cand(2, "Żabno")],
        manual={normalise_name("Żabno"): 2},
        min_score=88,
    )
    summary = match_summary(results)
    assert summary == {
        "total": 3,
        "matched": 2,
        "manual": 1,
        "published": 0,
        "fuzzy": 1,
        "rate": pytest.approx(2 / 3),
    }
    assert match_summary([])["rate"] == 0.0


def test_named_110kv_gpzs_and_nodes_are_candidates() -> None:
    subs = [
        Substation(1, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(0, 0), name="Piaski"),
        Substation(2, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(1, 0), name=None),
        Substation(3, SubstationClass.NODE_220_400, (400_000,), Point(2, 0), name="Węzeł"),
        Substation(4, SubstationClass.OTHER, (110_000,), Point(3, 0), name="Słup"),
    ]
    # a DSO GPZ can sit inside a PSE station (Energa's "Dunowo"), so named nodes count too
    assert [c.key for c in gpz_candidates(subs)] == [1, 3]


def test_numbers_in_a_name_must_be_identical() -> None:
    candidates = [_cand(1, "Gdańsk 1"), _cand(2, "Gdańsk 2")]
    (wrong,) = match_gpz_names("enea", ["Gdańsk 10"], candidates, min_score=88)
    assert wrong.candidate_key is None  # ratio 94 against "Gdańsk 1", but the number differs
    (right,) = match_gpz_names("enea", ["GPZ Gdańsk 2"], candidates, min_score=88)
    assert right.candidate_key == 2


def test_long_generic_phrases_and_voltage_pairs_are_dropped() -> None:
    assert normalise_name("Stacja elektroenergetyczna Piaski") == "piaski"
    assert normalise_name("GPZ Piaski 110/15 kV") == "piaski"
    assert normalise_name("GPZ Gdańsk 2") == "gdansk 2"  # a real number in the name stays


def test_tied_names_within_a_few_hundred_metres_are_one_station() -> None:
    node = GpzCandidate(7, "GPZ Piaski", Point(1000.0, 0.0))
    outline_centre = GpzCandidate(3, "Piaski", Point(1100.0, 0.0))  # the same GPZ mapped twice in OSM
    (result,) = match_gpz_names("enea", ["GPZ Piaski"], [node, outline_centre], min_score=88)
    assert result.method == "fuzzy"
    assert result.candidate_key == 3  # deterministic: the lowest key among equal scores
    far = GpzCandidate(9, "Piaski", Point(9000.0, 0.0))  # a different Piaski
    (ambiguous,) = match_gpz_names("enea", ["GPZ Piaski"], [node, far], min_score=88)
    assert ambiguous.candidate_key is None


def test_manual_mapping_may_target_an_unnamed_or_unclassified_station() -> None:
    named = [_cand(1, "Piaski")]
    (result,) = match_gpz_names(
        "enea", ["Żabno"], named, manual={normalise_name("Żabno"): 42}, manual_targets={42}, min_score=88
    )
    assert (result.candidate_key, result.method) == (42, "manual")
    with pytest.raises(ValueError, match="not a known substation"):
        match_gpz_names("enea", ["Żabno"], named, manual={normalise_name("Żabno"): 43}, manual_targets={42})


def test_osm_voltage_specs_prefixes_and_sections_are_not_part_of_the_name() -> None:
    # names as they appear in OSM and in the Energa / PSE lists (live data, 19.10.2026)
    assert normalise_name("Stacja elektroenergetyczna 110/15kV „GPZ Gdańsk Chełm”") == "gdansk chelm"
    assert normalise_name('Stacja elektroenergetyczna "Dunowo" 400/220/110 kV') == "dunowo"
    assert normalise_name("Stacja elektroenergetyczna 110/30/20/6kV GPZ „Będzin”") == "bedzin"
    assert normalise_name("Stacja 110kV Duszniki") == "duszniki"
    assert normalise_name("Baczyna Systemowa") == normalise_name(
        "Stacja elektroenergetyczna 400/220/110 kV Baczyna"
    )
    assert normalise_name("Kutno sekcja 2") == "kutno"
    assert normalise_name("Lindów 3") == "lindow 3"  # a plain number stays: it names another station


def test_trailing_roman_numeral_equals_the_digit() -> None:
    assert normalise_name("Stacja elektroenergetyczna 110/15kV „GPZ Gdańsk II”") == normalise_name("Gdańsk 2")
    assert normalise_name("Olsztyn I") == "olsztyn 1"
    assert normalise_name("I") == "i"  # a lone letter is a name, not a number


def test_wind_farm_station_is_not_the_dso_gpz() -> None:
    # "GPO" is a wind farm's own station: it must not tie with (or stand in for) the DSO's GPZ of that town
    candidates = [_cand(1, "Stacja 110/15kV Tychowo"), _cand(2, "Stacja 110kV GPO Tychowo")]
    (result,) = match_gpz_names("energa", ["Tychowo"], candidates, min_score=90)
    assert result.candidate_key == 1


def test_a_dso_name_never_matches_a_station_of_another_dso() -> None:
    candidates = [
        GpzCandidate(1, "Bojanowo", Point(0, 0), Dso.ENEA),
        GpzCandidate(2, "Bojanowo", Point(200_000, 0), Dso.ENERGA),
        GpzCandidate(3, "Dunowo", Point(400_000, 0), Dso.PSE),
        GpzCandidate(4, "Piaski", Point(600_000, 0), None),
    ]
    results = match_gpz_names("energa", ["Bojanowo", "Dunowo", "Piaski"], candidates, min_score=90)
    assert [r.candidate_key for r in results] == [2, 3, 4]  # PSE and untagged stations stay candidates
    # PSE lists are not a DSO: no operator filter
    (pse,) = match_gpz_names("pse_closed", ["Bojanowo"], candidates[:1], min_score=90)
    assert pse.candidate_key == 1


def test_one_station_keeps_only_its_best_name() -> None:
    candidates = [_cand(1, "GPZ Ostróda Wschód")]
    results = match_gpz_names("energa", ["Ostrów Wschód", "Ostróda Wschód"], candidates, min_score=85)
    assert [(r.candidate_key, r.method) for r in results] == [(None, "none"), (1, "fuzzy")]
    assert results[0].reason == "stacja OSM przypisana lepiej pasującej nazwie"
