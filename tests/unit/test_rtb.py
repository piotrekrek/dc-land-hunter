"""RTB workstream proposal: table-driven rules and the Rzepin site as stored on 12.11.2026."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dclh_pipeline.config import load_scoring
from dclh_pipeline.rtb import PowerEvidence, Workstream, propose_rag

TODAY = date(2026, 11, 12)
CFG = load_scoring()


def fact(value: Any, source: str = "test", confidence: str = "verified") -> dict[str, Any]:
    return {
        "value": value,
        "source_id": source,
        "as_of": "2026-11-01T00:00:00+00:00",
        "confidence": confidence,
    }


def clean_detail() -> dict[str, Any]:
    """A site that deserves green everywhere the data can decide: only power and the team's streams cap it."""
    return {
        "score": {"exclusions": [], "project_mw": 60.0, "split_suggested": False},
        "features": {
            "area_ha": fact(15.0, "egib"),
            "compactness": fact(0.6, "egib"),
            "parcel_count": fact(2, "egib"),
            "dist_line110_km": fact(0.4, "osm_power", "estimated"),
            "dist_gpz110_km": fact(2.0, "osm_power", "estimated"),
            "zoning": fact("mpzp_production", "ru"),
            "planning_path": fact("mpzp_in_force", "ru", "estimated"),
            "in_ouz": fact("yes", "ru"),
            "protected_nature_pct": fact(0.0, "gdos"),
            "flood_10_or_1_pct": fact(0.0, "isok"),
            "forest_pct": fact(0.0, "bdot10k"),
            "deposit_overlap_pct": fact(0.0, "midas"),
            "mining_area_pct": fact(0.0, "midas"),
        },
    }


def streams(detail: dict[str, Any], level: str = "rumour", **evidence: Any) -> dict[str, Workstream]:
    result = propose_rag(detail, PowerEvidence(level=level, **evidence), CFG, today=TODAY)
    return {w.key: w for w in result}


def test_six_workstreams_in_order() -> None:
    assert list(streams(clean_detail())) == [
        "land",
        "power",
        "planning",
        "environment",
        "concept",
        "commercial",
    ]


def test_clean_site_is_green_where_data_decides() -> None:
    s = streams(clean_detail(), "conditions_issued")
    assert {k: w.status for k, w in s.items()} == {
        "land": "green",
        "power": "green",
        "planning": "green",
        "environment": "green",
        "concept": "yellow",
        "commercial": "yellow",
    }
    assert "do oceny przez zespół" in s["concept"].reasons[-1].text_pl


@pytest.mark.parametrize(
    ("level", "extra", "expected"),
    [
        ("rumour", {}, "yellow"),
        ("informal_opinion", {}, "yellow"),
        ("application_filed", {}, "yellow"),
        ("conditions_issued", {"conditions_expiry": date(2027, 5, 1)}, "green"),
        ("conditions_issued", {"conditions_expiry": date(2026, 11, 1)}, "yellow"),  # expired
        ("agreement_signed", {}, "green"),
    ],
)
def test_power_is_capped_below_conditions_issued(level: str, extra: dict[str, Any], expected: str) -> None:
    assert streams(clean_detail(), level, **extra)["power"].status == expected


def test_power_is_red_without_110kv_in_range_whatever_the_evidence() -> None:
    d = clean_detail()
    d["features"]["dist_line110_km"] = fact(7.0, "osm_power")
    d["features"]["dist_gpz110_km"] = fact(12.0, "osm_power")
    assert streams(d, "agreement_signed")["power"].status == "red"


def test_power_is_yellow_when_distances_are_unknown() -> None:
    d = clean_detail()
    del d["features"]["dist_line110_km"], d["features"]["dist_gpz110_km"]
    assert streams(d, "agreement_signed")["power"].status == "yellow"


def test_power_names_the_evidence_level_and_its_date() -> None:
    p = streams(clean_detail(), "application_filed", document_date=date(2026, 10, 1))["power"]
    evidence = next(r for r in p.reasons if r.text_pl.startswith("Dowód mocy"))
    assert "Wniosek złożony" in evidence.text_pl
    assert (evidence.source_id, evidence.as_of) == ("power_evidence", "2026-10-01")


def test_large_campus_needs_a_node() -> None:
    d = clean_detail()
    d["score"]["project_mw"] = 200.0
    assert streams(d, "agreement_signed")["power"].status == "yellow"
    d["features"]["dist_node_220_400_km"] = fact(10.0, "osm_power")
    assert streams(d, "agreement_signed")["power"].status == "green"


def test_unknown_evidence_level_is_refused() -> None:
    with pytest.raises(ValueError, match="evidence level"):
        propose_rag(clean_detail(), PowerEvidence(level="nope"), CFG)


@pytest.mark.parametrize(
    ("zoning", "path", "expected"),
    [
        ("mpzp_production", "mpzp_in_force", "green"),
        ("plan_ogolny_SG", "mpzp_or_zpi", "yellow"),
        ("plan_ogolny_SG", "wz_possible", "yellow"),
        ("procedure_in_progress", "mpzp_or_zpi", "yellow"),
        ("unknown", None, "yellow"),
        ("other", "mpzp_in_force", "red"),
        ("other", None, "red"),
        (None, None, "yellow"),
    ],
)
def test_planning(zoning: str | None, path: str | None, expected: str) -> None:
    d = clean_detail()
    d["features"]["zoning"] = fact(zoning, "ru") if zoning else None
    d["features"]["planning_path"] = fact(path, "ru") if path else None
    assert streams(d)["planning"].status == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"area_ha": fact(8.0)}, "red"),
        ({"area_ha": None}, "yellow"),
        ({"compactness": fact(0.2)}, "yellow"),
        ({"parcel_count": fact(11)}, "yellow"),
        ({"parcel_count": fact(31)}, "red"),
        ({"parcel_count": fact(10)}, "green"),
    ],
)
def test_land(changes: dict[str, Any], expected: str) -> None:
    d = clean_detail()
    d["features"].update(changes)
    assert streams(d)["land"].status == expected


def test_deposit_overlap_caps_environment_at_yellow_and_names_the_deposit() -> None:
    d = clean_detail()
    d["features"]["deposit_overlap_pct"] = fact(63.0, "midas")
    d["features"]["deposit_list"] = fact(
        [{"name": "Rzepin", "status": "", "mineral": "WĘGLE BRUNATNE"}], "midas"
    )
    env = streams(d)["environment"]
    assert env.status == "yellow"
    reason = next(r for r in env.reasons if "Złoże" in r.text_pl)
    assert "Rzepin" in reason.text_pl and "63%" in reason.text_pl
    assert reason.source_id == "midas"


def test_deposit_overlap_without_a_name_still_blocks_green() -> None:
    d = clean_detail()
    d["features"]["deposit_overlap_pct"] = fact(5.0, "midas")
    assert streams(d)["environment"].status == "yellow"


@pytest.mark.parametrize(
    ("key", "mode", "expected"),
    [
        ("flood_10_or_1_pct", "exclude", "red"),
        ("protected_nature_pct_over", "exclude", "red"),
        ("forest_pct_over", "exclude", "red"),
        ("flood_10_or_1_pct", "penalty", "yellow"),
    ],
)
def test_environmental_exclusions(key: str, mode: str, expected: str) -> None:
    d = clean_detail()
    d["score"]["exclusions"] = [{"key": key, "reason_pl": "powód", "mode": mode, "penalty_points": 30.0}]
    d["features"]["deposit_overlap_pct"] = fact(10.0, "midas")  # a deposit never lowers a red
    assert streams(d)["environment"].status == expected


def test_environment_is_yellow_when_a_constraint_is_unknown() -> None:
    d = clean_detail()
    d["features"]["flood_10_or_1_pct"] = None
    assert streams(d)["environment"].status == "yellow"


def test_close_housing_excluded_makes_concept_red() -> None:
    d = clean_detail()
    d["score"]["exclusions"] = [
        {"key": "residential_within_m", "reason_pl": "zabudowa", "mode": "exclude", "penalty_points": 20.0}
    ]
    assert streams(d)["concept"].status == "red"


def test_every_reason_with_a_number_has_a_source() -> None:
    for w in streams(clean_detail(), "conditions_issued").values():
        for r in w.reasons:
            if any(c.isdigit() for c in r.text_pl):
                assert r.source_id, r.text_pl


def test_rzepin_as_stored() -> None:
    """The site that motivated the export: lignite deposit, plan ogólny outside the OUZ, no power paper."""
    path = Path(__file__).parents[1] / "fixtures" / "rzepin_site_detail.json"
    detail = json.loads(path.read_text(encoding="utf-8"))
    s = streams(copy.deepcopy(detail))
    assert s["environment"].status == "yellow"
    assert any("Rzepin" in r.text_pl and "63%" in r.text_pl for r in s["environment"].reasons)
    assert s["power"].status == "yellow"
    assert s["planning"].status == "yellow"
    assert s["concept"].status == "yellow" and s["commercial"].status == "yellow"
    assert streams(detail, "conditions_issued")["power"].status == "green"
