"""Login, sessions and route protection. Needs PostGIS (skipped without it)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError

from dclh_api.main import create_app
from dclh_api.security import LoginThrottle, login_throttle
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
from dclh_pipeline.settings import get_settings

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"
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


# --------------------------------------------------------------------------- with database


@pytest.fixture(scope="module")
def db() -> Iterator[None]:
    url = get_settings().database_url
    try:
        with create_engine(url, connect_args={"connect_timeout": 2}).connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip(f"no database at {url}")
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with session_scope() as s:
        create_user(s, "Admin@Example.org", PASSWORD, role="admin")
        create_user(s, "member@example.org", PASSWORD)
    yield


@pytest.fixture
def client(db: None) -> Iterator[TestClient]:
    login_throttle._failures.clear()
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
