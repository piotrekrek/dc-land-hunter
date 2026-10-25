"""The nightly plan only queues powiats that reach the stored AOI (brief 2.1), read from the `aoi` table."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from dclh_pipeline import jobs
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow
from dclh_pipeline.powiat_search import powiats_outside_aoi

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]


def box(x0: int, x1: int) -> str:
    return f"ST_Multi(ST_GeomFromText('POLYGON(({x0} 0, {x1} 0, {x1} 10, {x0} 10, {x0} 0))', 2180))"


# AOI = x 0..100. West sits inside, "river" straddles the edge, east is outside, edge only touches it.
POWIATS = {"0001": box(10, 20), "0002": box(90, 110), "0003": box(200, 210), "0004": box(100, 150)}


@pytest.fixture(autouse=True)
def tables() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("jobs", "powiat_priority", "search_runs", "admin_units", "aoi"):
                session.execute(text(f"DELETE FROM {table}"))

    wipe()
    with session_scope() as session:
        for teryt, geom in POWIATS.items():
            session.execute(
                text(f"INSERT INTO admin_units (teryt, level, name, geom) VALUES (:t, 'powiat', :t, {geom})"),
                {"t": teryt},
            )
    yield
    wipe()


def load_aoi() -> None:
    with session_scope() as session:
        session.execute(text(f"INSERT INTO aoi (geom) VALUES ({box(0, 100)})"))


def test_outside_means_not_reaching_the_aoi_and_a_straddling_powiat_is_inside() -> None:
    load_aoi()
    with session_scope() as session:
        assert powiats_outside_aoi(session) == {"0003", "0004"}


def test_with_no_aoi_loaded_nothing_is_outside() -> None:
    with session_scope() as session:
        assert powiats_outside_aoi(session) == set()


def plan(monkeypatch: pytest.MonkeyPatch, **payload: object) -> list[str]:
    monkeypatch.setattr(jobs, "SnapshotStore", lambda _raw: SimpleNamespace(latest=lambda _id: object()))
    monkeypatch.setattr(jobs, "powiats_from_prg", lambda _snapshot: sorted(POWIATS))
    with session_scope() as session:
        result = jobs._job_plan_searches(session, {"limit": 10, **payload})
        queued = [
            str(p["powiat"]) for p in session.scalars(select(JobRow.payload).where(JobRow.kind == "search"))
        ]
    assert sorted(result["queued"]) == sorted(queued)
    return sorted(queued)


def test_the_plan_skips_powiats_outside_the_aoi(monkeypatch: pytest.MonkeyPatch) -> None:
    load_aoi()
    assert plan(monkeypatch) == ["0001", "0002"]


def test_a_priority_powiat_outside_the_aoi_is_not_searched(monkeypatch: pytest.MonkeyPatch) -> None:
    load_aoi()
    assert plan(monkeypatch, priority=["0003"]) == ["0001", "0002"]


def test_without_an_aoi_the_plan_searches_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    assert plan(monkeypatch) == ["0001", "0002", "0003", "0004"]
