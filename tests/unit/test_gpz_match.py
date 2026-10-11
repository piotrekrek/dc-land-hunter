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
from dclh_pipeline.model.grid import SubstationClass


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
    with pytest.raises(ValueError, match="not a GPZ candidate"):
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
    assert summary == {"total": 3, "matched": 2, "manual": 1, "fuzzy": 1, "rate": pytest.approx(2 / 3)}
    assert match_summary([])["rate"] == 0.0


def test_only_named_110kv_gpzs_are_candidates() -> None:
    subs = [
        Substation(1, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(0, 0), name="Piaski"),
        Substation(2, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(1, 0), name=None),
        Substation(3, SubstationClass.NODE_220_400, (400_000,), Point(2, 0), name="Węzeł"),
        Substation(4, SubstationClass.OTHER, (110_000,), Point(3, 0), name="Słup"),
    ]
    assert [c.key for c in gpz_candidates(subs)] == [1]
