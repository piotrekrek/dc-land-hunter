"""Shape checks for the golden set. The real check (scores per site) arrives with scoring in Phase 2."""

from __future__ import annotations

from pathlib import Path

import yaml

GOLDEN = Path(__file__).parents[1] / "golden" / "sites.yaml"


def test_golden_set_matches_brief() -> None:
    sites = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["sites"]
    by_expect: dict[str, set[str]] = {}
    for s in sites:
        by_expect.setdefault(s["expect"], set()).add(s["name"])
    assert len(by_expect["not_excluded_top20pct"]) == 4
    assert by_expect["excluded_too_small"] == {"Data4 Jawczyce", "Atman Duchnice", "Switch Mory"}


def test_unverified_coordinates_stay_empty() -> None:
    """Coordinates may only be filled once verified on Geoportal (no guesses in the golden set)."""
    for s in yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["sites"]:
        if not s["verified_on_geoportal"]:
            assert s["lon"] is None and s["lat"] is None, s["name"]
        else:
            assert 14.0 < s["lon"] < 24.2 and 49.0 < s["lat"] < 54.9, s["name"]
            assert s["sources"], f"{s['name']}: cite where the location comes from"
