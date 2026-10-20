"""Login, sessions and route protection. Needs PostGIS (skipped without it)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from dclh_api.deps import db_session
from dclh_api.main import create_app
from dclh_api.routers import auth as auth_router
from dclh_api.security import (
    LOGIN_IP_MAX_FAILURES,
    LoginThrottle,
    admin_user,
    current_user,
    login_ip_throttle,
    login_throttle,
)
from dclh_pipeline.auth import (
    PasswordTooShortError,
    UserExistsError,
    authenticate,
    create_session,
    create_user,
    hash_password,
    set_password,
    user_for_token,
    verify_password,
)
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, SessionRow

PASSWORD = "correct horse battery"


# --------------------------------------------------------------------------- pure logic (no database)


def test_password_hash_roundtrip() -> None:
    h = hash_password(PASSWORD)
    assert h != PASSWORD and h.startswith("$argon2id$")
    assert verify_password(h, PASSWORD)
    assert not verify_password(h, "wrong password!!")
    assert not verify_password("not-a-hash", PASSWORD)


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(PasswordTooShortError):
        hash_password("short")


def test_login_throttle_blocks_then_expires() -> None:
    now = [0.0]
    t = LoginThrottle(max_failures=3, window_s=100, clock=lambda: now[0])
    for _ in range(3):
        assert not t.blocked("k")
        t.record_failure("k")
    assert t.blocked("k") and not t.blocked("other")
    now[0] = 101
    assert not t.blocked("k")
    t.record_failure("k")
    t.reset("k")
    assert not t.blocked("k")


def test_login_throttle_does_not_keep_unused_or_expired_keys() -> None:
    now = [0.0]
    t = LoginThrottle(max_failures=3, window_s=100, clock=lambda: now[0])
    for i in range(1000):
        assert not t.blocked(f"probe-{i}")
    assert t._failures == {}
    t.record_failure("k")
    now[0] = 101
    assert not t.blocked("k")
    assert t._failures == {}


def _app_without_database(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The API with the database and the logged-in user stubbed out: runs without PostGIS."""
    app = create_app()
    app.dependency_overrides[db_session] = lambda: MagicMock()
    admin = SimpleNamespace(email="admin@example.org", role="admin")
    app.dependency_overrides[current_user] = lambda: admin
    app.dependency_overrides[admin_user] = lambda: admin
    monkeypatch.setattr(auth_router, "authenticate", lambda *_args: None)
    login_throttle._failures.clear()
    login_ip_throttle._failures.clear()
    return app


def test_login_ip_cap_blocks_spraying_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_app_without_database(monkeypatch))
    for i in range(LOGIN_IP_MAX_FAILURES):  # one try per email never trips the per-(email, IP) block
        r = client.post(
            "/api/auth/login", json={"email": f"spray{i}@example.org", "password": "wrong-password"}
        )
        assert r.status_code == 401, i
    r = client.post("/api/auth/login", json={"email": "anyone@example.org", "password": "wrong-password"})
    assert r.status_code == 429


def test_discovery_is_refused_while_a_job_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app_without_database(monkeypatch)
    busy = MagicMock()
    busy.scalars.return_value.first.return_value = 7  # an earlier discover job is pending or running
    app.dependency_overrides[db_session] = lambda: busy
    r = TestClient(app).post("/api/sources/discover", json={"sources": []})
    assert r.status_code == 409
    assert "już trwa" in r.json()["detail"]
    busy.add.assert_not_called()  # nothing was queued


# --------------------------------------------------------------------------- with database


@pytest.fixture(scope="module")
def db(migrated_db: None) -> Iterator[None]:
    with session_scope() as s:
        create_user(s, "Admin@Example.org", PASSWORD, role="admin")
        create_user(s, "member@example.org", PASSWORD)
    yield


@pytest.fixture
def client(db: None) -> Iterator[TestClient]:
    login_throttle._failures.clear()
    login_ip_throttle._failures.clear()
    with TestClient(create_app()) as c:
        yield c


def login(client: TestClient, email: str, password: str = PASSWORD) -> None:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


@pytest.mark.db
def test_emails_are_normalised_and_unique(db: None) -> None:
    with session_scope() as s, pytest.raises(UserExistsError):
        create_user(s, "  ADMIN@example.org ", PASSWORD)


@pytest.mark.db
def test_protected_routes_need_login(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200  # public: used by container health checks
    for path in ("/api/sources", "/api/config/scoring", "/api/auth/me"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/sources/discover", json={}).status_code == 401


@pytest.mark.db
def test_login_sets_http_only_cookie_and_me_works(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"email": "admin@example.org", "password": PASSWORD})
    assert r.json() == {"email": "admin@example.org", "role": "admin"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["email"] == "admin@example.org"
    assert client.get("/api/sources").status_code == 200


@pytest.mark.db
def test_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    a = client.post("/api/auth/login", json={"email": "admin@example.org", "password": "x" * 14})
    b = client.post("/api/auth/login", json={"email": "nobody@example.org", "password": "x" * 14})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert "set-cookie" not in a.headers


@pytest.mark.db
def test_repeated_failures_are_throttled(client: TestClient) -> None:
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"email": "member@example.org", "password": "bad"}
            ).status_code
            == 401
        )
    r = client.post("/api/auth/login", json={"email": "member@example.org", "password": PASSWORD})
    assert r.status_code == 429  # even the right password is refused while blocked


@pytest.mark.db
def test_one_address_cannot_spray_many_emails(client: TestClient) -> None:
    # Each email fails only once, so the per-(email, IP) block never trips; the per-IP cap must.
    for i in range(LOGIN_IP_MAX_FAILURES):
        r = client.post(
            "/api/auth/login", json={"email": f"spray{i}@example.org", "password": "wrong-password"}
        )
        assert r.status_code == 401, i
    r = client.post("/api/auth/login", json={"email": "member@example.org", "password": PASSWORD})
    assert r.status_code == 429  # a known account is refused too, from the blocked address


@pytest.mark.db
def test_logout_invalidates_the_session(client: TestClient) -> None:
    login(client, "member@example.org")
    token = client.cookies.get("dclh_session")
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("dclh_session", token or "")  # replay the old cookie
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.db
def test_only_hashes_are_stored(client: TestClient) -> None:
    login(client, "member@example.org")
    token = client.cookies.get("dclh_session")
    with session_scope() as s:
        hashes = list(s.scalars(select(SessionRow.token_hash)))
    assert token and token not in hashes and all(len(h) == 64 for h in hashes)


@pytest.mark.db
def test_expired_session_is_rejected(db: None) -> None:
    with session_scope() as s:
        user = authenticate(s, "member@example.org", PASSWORD)
        assert user is not None
        past = datetime.now(UTC) - timedelta(days=15)
        token, _ = create_session(s, user, now=past)
    with session_scope() as s:
        assert user_for_token(s, token) is None
        assert user_for_token(s, token, now=past + timedelta(days=1)) is not None


@pytest.mark.db
def test_password_change_signs_out_everywhere(client: TestClient) -> None:
    with session_scope() as s:
        create_user(s, "temp@example.org", PASSWORD)
    login(client, "temp@example.org")
    with session_scope() as s:
        set_password(s, "temp@example.org", "another long password")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post(
        "/api/auth/login", json={"email": "temp@example.org", "password": "another long password"}
    )
    assert r.status_code == 200


@pytest.mark.db
def test_run_discovery_is_admin_only_and_queues_a_job(client: TestClient) -> None:
    login(client, "member@example.org")
    assert client.post("/api/sources/discover", json={"sources": ["gdos"]}).status_code == 403
    client.post("/api/auth/logout")

    login(client, "admin@example.org")
    assert client.post("/api/sources/discover", json={"sources": ["nope"]}).status_code == 422
    r = client.post("/api/sources/discover", json={"sources": ["gdos"]})
    assert r.status_code == 202 and r.json()["status"] == "pending"
    with session_scope() as s:
        job = s.get(JobRow, r.json()["id"])
        assert (
            job is not None and job.payload["sources"] == ["gdos"] and job.requested_by == "admin@example.org"
        )


@pytest.mark.db
def test_discovery_is_refused_while_one_is_pending_or_running(client: TestClient) -> None:
    login(client, "admin@example.org")
    with session_scope() as s:  # earlier tests leave discovery jobs queued
        for old in s.scalars(
            select(JobRow).where(JobRow.kind == "discover", JobRow.status.in_(("pending", "running")))
        ):
            old.status = "done"

    first = client.post("/api/sources/discover", json={"sources": []})
    assert first.status_code == 202
    for status_now in ("pending", "running"):
        with session_scope() as s:
            job = s.get(JobRow, first.json()["id"])
            assert job is not None
            job.status = status_now
        r = client.post("/api/sources/discover", json={"sources": []})
        assert r.status_code == 409, status_now
        assert "już trwa" in r.json()["detail"]

    with session_scope() as s:
        job = s.get(JobRow, first.json()["id"])
        assert job is not None
        job.status = "done"
    assert client.post("/api/sources/discover", json={"sources": []}).status_code == 202
