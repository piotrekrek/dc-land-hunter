"""Tests against a real PostGIS database (skipped when DATABASE_URL is unreachable)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dclh_api.main import create_app
from dclh_pipeline.config import load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, RunRow, SourceRow
from dclh_pipeline.jobs import claim_next, enqueue, execute, sync_sources
from dclh_pipeline.settings import get_settings

pytestmark = pytest.mark.db

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> Iterator[None]:
    url = get_settings().database_url
    try:
        with create_engine(url, connect_args={"connect_timeout": 2}).connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip(f"no database at {url}")
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


def test_health_ok_with_postgis() -> None:
    with TestClient(create_app()) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["postgis_version"].startswith("3.")


def test_sync_sources_is_idempotent() -> None:
    specs = load_sources().sources
    with session_scope() as session:
        sync_sources(session, specs)
        sync_sources(session, specs)
    with session_scope() as session:
        assert session.query(SourceRow).count() == len(specs)


def test_job_queue_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    with session_scope() as session:
        job_id = enqueue(
            session, "discover", {"sources": ["ekw"], "ran_from": "test"}
        ).id  # manual: no network
    with session_scope() as session:
        claimed = claim_next(session)
        assert claimed is not None and claimed.id == job_id and claimed.status == "running"
    execute(job_id)
    with session_scope() as session:
        job = session.get(JobRow, job_id)
        assert job is not None and job.status == "done", job.error if job else None
        run = session.get(RunRow, job.run_id)
        assert run is not None and run.status == "ok"
        assert run.summary["status_counts"] == {"not_checked": 1}
        ekw = session.get(SourceRow, "ekw")
        assert ekw is not None and ekw.discovery_status == "not_checked"
    assert list((tmp_path / "discovery").glob("discovery-*.json"))


def test_interrupted_running_job_is_failed_on_worker_start() -> None:
    from dclh_pipeline.jobs import fail_interrupted

    with session_scope() as session:
        job_id = enqueue(session, "discover", {"sources": ["ekw"]}).id
    with session_scope() as session:
        claimed = claim_next(session)
        assert claimed is not None and claimed.id == job_id
        run = RunRow(kind="discover", status="running")
        session.add(run)
        session.flush()
        claimed.run_id = run.id
        run_id = run.id
    with session_scope() as session:
        assert fail_interrupted(session) >= 1
    with session_scope() as session:
        job = session.get(JobRow, job_id)
        run_row = session.get(RunRow, run_id)
        assert job is not None and job.status == "failed" and job.finished_at is not None
        assert run_row is not None and run_row.status == "failed" and run_row.finished_at is not None


def test_unknown_job_kind_rejected() -> None:
    with session_scope() as session, pytest.raises(ValueError, match="unknown job kind"):
        enqueue(session, "launch_rockets")


def test_sources_endpoint_reads_db_state() -> None:
    from dclh_pipeline.auth import create_user

    with session_scope() as session:
        create_user(session, "reader@example.org", "a long enough password")
    with TestClient(create_app()) as client:
        r = client.post(
            "/api/auth/login", json={"email": "reader@example.org", "password": "a long enough password"}
        )
        assert r.status_code == 200
        body = client.get("/api/sources").json()
    assert body["database"] is True
    ekw = next(s for s in body["sources"] if s["id"] == "ekw")
    assert ekw["discovery_status"] == "not_checked"


def test_phase1_tables_and_map_views() -> None:
    from sqlalchemy import text

    from dclh_pipeline.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO substations (osm_id, name, dso, cls, confidence, voltages, geom) VALUES "
                "(1, 'GPZ Test', 'enea', 'GPZ_110_SN', 'estimated', '{110000,15000}', ST_SetSRID(ST_MakePoint(360000, 500000), 2180))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO lines (osm_id, voltage, confidence, geom) VALUES "
                "(10, 110000, 'estimated', ST_SetSRID(ST_MakeLine(ST_MakePoint(0,0), ST_MakePoint(1000,0)), 2180)),"
                "(11, 15000, 'estimated', ST_SetSRID(ST_MakeLine(ST_MakePoint(0,0), ST_MakePoint(1000,0)), 2180))"
            )
        )
        assert conn.execute(text("SELECT count(*) FROM tiles.substations")).scalar_one() == 1
        # The map only shows 110 kV and above.
        assert conn.execute(text("SELECT count(*) FROM tiles.lines")).scalar_one() == 1
        with pytest.raises(Exception, match="cls"):
            conn.execute(
                text(
                    "INSERT INTO substations (osm_id, cls, confidence, geom) VALUES "
                    "(2, 'BOGUS', 'estimated', ST_SetSRID(ST_MakePoint(1, 1), 2180))"
                )
            )
