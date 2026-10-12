from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dclh_pipeline.config import ScoringConfig, load_scoring, load_sources

# Source ids from PROJECT_BRIEF.md section 4, by plan.
BRIEF_MVP = {
    "prg", "osm_power", "osm_land", "bdot10k", "ru", "kimpzp", "egib", "uldk", "kiug", "gdos", "isok",
    "midas", "nid", "pse_closed", "pse_odb", "pse_avail", "enea", "energa", "tauron", "uke", "psi",
    "invest_offers", "heat",
}  # fmt: skip
BRIEF_V2 = {"dso_portals", "bip"}
BRIEF_MANUAL = {"bazaoos", "ekw", "kowr"}
# Sources fed only by manual imports, so there is nothing to probe.
NO_PROBES = {"invest_offers"}
# Core sources gate score publishing (brief section 10).
BRIEF_CORE = {"osm_power", "egib", "ru", "gdos", "isok"}


def test_scoring_defaults_match_brief() -> None:
    cfg = load_scoring()
    assert cfg.target.mw_min == 60
    assert cfg.target.mw_per_ha == 6
    assert cfg.target.ha_min == 10
    assert cfg.weights.model_dump() == {"grid": 45, "zoning": 25, "land": 15, "connectivity": 5, "bonus": 10}
    assert (
        cfg.prefilter.line110_km == 3 and cfg.prefilter.gpz110_km == 5 and cfg.prefilter.node_220_400_km == 25
    )
    assert cfg.connection_cost.verified is False  # needs legal verification


def test_connection_cost_example_from_brief() -> None:
    """60 MW: about 60k PLN fees, 3.6 mln PLN advance and 1.8 mln PLN security (brief section 3)."""
    cc = load_scoring().connection_cost
    kw = 60_000
    assert min(kw * cc.application_fee_pln_per_kw, cc.application_fee_max_pln) == 60_000
    assert min(kw * cc.advance_pln_per_kw, cc.advance_max_pln) == 3_600_000
    assert kw * cc.security_pln_per_kw == 1_800_000


def test_weights_must_sum_to_100(tmp_path: Path) -> None:
    raw = yaml.safe_load((Path(__file__).parents[2] / "config" / "scoring.yaml").read_text(encoding="utf-8"))
    raw["weights"]["grid"] = 50
    with pytest.raises(ValidationError, match="sum to 100"):
        ScoringConfig.model_validate(raw)


def test_distance_ranges_are_ordered() -> None:
    raw = yaml.safe_load((Path(__file__).parents[2] / "config" / "scoring.yaml").read_text(encoding="utf-8"))
    raw["grid"]["line110_km_full"] = 5
    with pytest.raises(ValidationError, match="line110_km_full"):
        ScoringConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("section", "field", "value", "match"),
    [
        ("connectivity", "telecom_node_km_full", 20, "telecom_node_km_full"),
        ("connectivity", "city_km_full", 200, "city_km_full"),
        ("land", "parcels_full", 40, "parcels_full"),
    ],
)
def test_other_ranges_are_ordered(section: str, field: str, value: float, match: str) -> None:
    raw = yaml.safe_load((Path(__file__).parents[2] / "config" / "scoring.yaml").read_text(encoding="utf-8"))
    raw[section][field] = value
    with pytest.raises(ValidationError, match=match):
        ScoringConfig.model_validate(raw)


def test_sources_cover_the_brief() -> None:
    cfg = load_sources()
    assert {s.id for s in cfg.by_plan("mvp")} == BRIEF_MVP
    assert {s.id for s in cfg.by_plan("v2")} == BRIEF_V2
    assert {s.id for s in cfg.by_plan("manual")} == BRIEF_MANUAL
    assert {s.id for s in cfg.sources if s.core} == BRIEF_CORE


def test_every_mvp_source_has_probes() -> None:
    missing = [s.id for s in load_sources().by_plan("mvp") if not s.probes and s.id not in NO_PROBES]
    assert missing == []


def test_manual_sources_are_links_only() -> None:
    for spec in load_sources().by_plan("manual"):
        assert spec.manual_url is not None
        assert spec.probes == []


def test_politeness_default() -> None:
    assert load_sources().defaults.rate_limit_per_s <= 1.0
