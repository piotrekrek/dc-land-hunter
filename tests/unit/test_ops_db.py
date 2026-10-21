"""The admin job-health endpoint against a real database (the rules live in SQL)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

HOUR = timedelta(hours=1)


def as_user(role: str) -> TestClient:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role=role)
    return TestClient(app)


@pytest.fixture
def admin() -> Iterator[TestClient]:
    with as_user("admin") as c:
        yield c


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(text("DELETE FROM jobs"))
            session.execute(text("DELETE FROM admin_units"))

    wipe()
    yield
    wipe()


def add_job(
    kind: str,
    status: str,
    *,
    powiat: str | None = None,
    age: timedelta = timedelta(0),
    error: str | None = None,
) -> None:
    payload = json.dumps({"powiat": powiat} if powiat else {})
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO jobs (kind, status, payload, error, created_at, finished_at) VALUES "
                "(:kind, :status, CAST(:payload AS jsonb), :error, now() - :age, now() - :age)"
            ),
            {"kind": kind, "status": status, "payload": payload, "error": error, "age": age},
        )


def get(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/ops/jobs")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_a_member_gets_403_and_no_job_data() -> None:
    with as_user("member") as member:
        assert member.get("/api/ops/jobs").status_code == 403


def test_an_empty_queue_needs_no_attention(admin: TestClient) -> None:
    body = get(admin)
    assert body["counts"] == {"pending": 0, "running": 0, "done": 0, "failed": 0}
    assert body["attention"] == 0 and body["failing_powiats"] == [] and body["recent"] == []


def test_counts_cover_the_last_24_hours_but_every_open_job(admin: TestClient) -> None:
    add_job("search", "done", powiat="0201")
    add_job("search", "failed", powiat="0202", error="boom")
    add_job("search", "done", powiat="0203", age=30 * HOUR)  # too old to count
    add_job("search", "pending", powiat="0204", age=30 * HOUR)  # open jobs always count
    add_job("search", "running", powiat="0205")
    assert get(admin)["counts"] == {"pending": 1, "running": 1, "done": 1, "failed": 1}


def test_a_powiat_fails_only_while_its_latest_search_failed(admin: TestClient) -> None:
    add_job("search", "failed", powiat="0201", age=3 * HOUR, error="old failure")
    add_job("search", "done", powiat="0201", age=2 * HOUR)  # recovered
    add_job("search", "done", powiat="0202", age=3 * HOUR)
    add_job("search", "failed", powiat="0202", age=2 * HOUR, error="IntegrityError: duplicate key")
    add_job("search", "failed", powiat="0203", age=3 * HOUR, error="older failure")
    add_job("search", "running", powiat="0203")  # a retry in flight must not hide the failure
    body = get(admin)
    assert [(p["teryt"], p["error"]) for p in body["failing_powiats"]] == [
        ("0202", "IntegrityError: duplicate key"),
        ("0203", "older failure"),
    ]
    assert body["attention"] == 2


def test_attention_adds_failed_non_search_jobs_of_the_last_day_only(admin: TestClient) -> None:
    add_job("search", "failed", powiat="0201", error="x")
    add_job("discover", "failed", error="y")
    add_job("discover", "failed", age=48 * HOUR, error="stale")
    assert get(admin)["attention"] == 2  # one failing powiat + one recent discover failure


def test_recent_jobs_are_newest_first_with_powiat_name_and_a_cut_error(admin: TestClient) -> None:
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO admin_units (teryt, level, name, geom) VALUES ('0201', 'powiat', 'powiat bolesławiecki', "
                "ST_Multi(ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 0))', 2180)))"
            )
        )
    add_job("search", "failed", powiat="0201", age=HOUR, error="e" * 1_000)
    add_job("discover", "done")
    recent = get(admin)["recent"]
    assert [j["kind"] for j in recent] == ["discover", "search"]
    assert recent[1]["powiat"] == "0201" and recent[1]["powiat_name"] == "powiat bolesławiecki"
    assert len(recent[1]["error"]) == 300
