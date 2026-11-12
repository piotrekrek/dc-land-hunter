"""RTB export pieces that need no database: red flags, the facts table and the evidence body."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dclh_api.schemas import PowerEvidenceBody
from dclh_pipeline.rtb_facts import FACT_SPECS, facts_table, red_flags

DETAIL: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "rzepin_site_detail.json").read_text(encoding="utf-8")
)


def test_rzepin_red_flags_are_the_fired_penalties() -> None:
    flags = {f.key: f for f in red_flags(DETAIL)}
    assert set(flags) == {"pen_mineral_deposit", "pen_residential"}
    deposit = flags["pen_mineral_deposit"]
    assert deposit.kind == "penalty" and deposit.points == 8
    assert (deposit.source_id, deposit.confidence) == ("midas", "verified")
    assert deposit.as_of is not None


def test_a_hard_exclusion_is_a_red_flag_with_the_deciding_fact_as_source() -> None:
    detail = {
        "score": {"exclusions": [{"key": "forest_pct_over", "reason_pl": "las: 40%", "mode": "exclude"}]},
        "features": {
            "forest_pct": {
                "value": 40.0,
                "source_id": "bdot10k",
                "as_of": "2026-11-01",
                "confidence": "verified",
            }
        },
    }
    (flag,) = red_flags(detail)
    assert (flag.kind, flag.points, flag.source_id, flag.as_of) == ("exclusion", 0.0, "bdot10k", "2026-11-01")


def test_no_penalties_no_red_flags() -> None:
    assert red_flags({"score": {"exclusions": [], "items": []}, "features": {}}) == []


def test_every_fact_row_has_a_source_and_a_confidence() -> None:
    rows = facts_table(DETAIL, scored_at="2026-11-12T07:00:00+00:00")
    assert len(rows) == len(FACT_SPECS) + 2
    for row in rows:
        assert row.source_id, row.key
        assert row.confidence in {"verified", "estimated", "missing"}
        if row.confidence != "missing":
            assert row.value is not None, row.key


def test_unknown_facts_are_listed_as_missing_not_dropped() -> None:
    by_key = {r.key: r for r in facts_table(DETAIL)}
    assert by_key["on_heritage_register"].confidence == "missing"
    assert by_key["on_heritage_register"].value is None
    assert by_key["deposit_overlap_pct"].value == pytest.approx(63.084)
    assert by_key["deposit_list"].value == "Rzepin"


def test_land_capacity_is_an_estimate_dated_by_the_scoring_run() -> None:
    by_key = {r.key: r for r in facts_table(DETAIL, scored_at="2026-11-12T07:00:00+00:00")}
    assert by_key["estimated_mw"].confidence == "estimated"
    assert by_key["estimated_mw"].as_of == "2026-11-12T07:00:00+00:00"
    assert by_key["project_mw"].value == 60.0


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://x.pl/a", "https://", "https://a b.pl", "nie link"]
)
def test_attachment_link_must_be_http_or_https(url: str) -> None:
    with pytest.raises(ValidationError):
        PowerEvidenceBody(level="conditions_issued", attachment_url=url)


def test_a_blank_link_is_no_link_and_a_good_one_is_trimmed() -> None:
    assert PowerEvidenceBody(level="rumour", attachment_url="  ").attachment_url is None
    body = PowerEvidenceBody(level="rumour", attachment_url=" https://drive.example.org/f/1 ")
    assert body.attachment_url == "https://drive.example.org/f/1"


@pytest.mark.parametrize(
    ("level", "ok"),
    [
        ("rumour", False),
        ("informal_opinion", False),
        ("application_filed", False),
        ("conditions_issued", True),
        ("agreement_signed", True),
    ],
)
def test_expiry_belongs_to_granted_conditions_only(level: str, ok: bool) -> None:
    def build() -> PowerEvidenceBody:
        return PowerEvidenceBody(level=level, conditions_expiry=date(2027, 5, 1))  # type: ignore[arg-type]

    if ok:
        assert build().conditions_expiry == date(2027, 5, 1)
    else:
        with pytest.raises(ValidationError):
            build()


def test_unknown_level_is_refused() -> None:
    with pytest.raises(ValidationError):
        PowerEvidenceBody(level="verbal_promise")  # type: ignore[arg-type]
