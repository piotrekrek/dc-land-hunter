"""A search run's summary keeps the time per step and the cache use, not only the total."""

from __future__ import annotations

from datetime import UTC, datetime

from dclh_pipeline.jobs import search_summary
from dclh_pipeline.powiat_search import PowiatSearch
from dclh_pipeline.search import SearchReport, SearchResult


def test_summary_has_steps_and_cache() -> None:
    report = SearchReport(tiles=40, parcels_fetched=1200, scored=30, excluded=12)
    report.seconds.update({"seed": 1.04, "parcels": 3600.26, "score": 95.0})
    found = PowiatSearch(
        teryt="0812",
        name="powiat świebodziński",
        result=SearchResult([], report),
        scored_at=datetime(2026, 11, 5, tzinfo=UTC),
        seconds=3700.33,
        cache_hits=900,
        cache_misses=300,
    )
    assert search_summary(found, sites=18) == {
        "powiat": "0812",
        "name": "powiat świebodziński",
        "sites": 18,
        "scored": 30,
        "excluded": 12,
        "parcels_fetched": 1200,
        "tiles": 40,
        "seconds": 3700.3,
        "step_seconds": {"seed": 1.0, "parcels": 3600.3, "score": 95.0},
        "cache": {"hits": 900, "misses": 300},
    }


def test_summary_of_a_search_with_no_parcels() -> None:
    found = PowiatSearch(
        teryt="0201",
        name="powiat bolesławiecki",
        result=SearchResult([], SearchReport()),
        scored_at=datetime(2026, 11, 5, tzinfo=UTC),
        seconds=2.0,
    )
    summary = search_summary(found, sites=0)
    assert summary["step_seconds"] == {}
    assert summary["cache"] == {"hits": 0, "misses": 0}
