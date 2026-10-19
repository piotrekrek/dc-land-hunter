"""The guard that keeps the schema-wiping database tests away from real databases."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import TEST_DB_ENV, ensure_safe_test_database, opt_in_database_url, sites_has_rows

PROD_LIKE = "postgresql+psycopg://dclh:secret@127.0.0.1:55432/dclh"


def test_db_tests_skip_without_the_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TEST_DB_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", PROD_LIKE)  # a DATABASE_URL alone is never enough
    with pytest.raises(pytest.skip.Exception, match=TEST_DB_ENV):
        opt_in_database_url()


@pytest.mark.parametrize(
    "url",
    [
        PROD_LIKE,
        "postgresql+psycopg://dclh:dclh@localhost:5432/dclh",
        "postgresql+psycopg://dclh:dclh@localhost:5432/dclh_test_backup",
        "postgresql+psycopg://dclh:dclh@localhost:5432",
    ],
)
def test_guard_refuses_database_names_without_the_test_suffix(url: str) -> None:
    # Fails before connecting: none of these hosts need to exist.
    with pytest.raises(pytest.exit.Exception, match="must name a database ending in _test") as exc:
        ensure_safe_test_database(url)
    assert "secret" not in str(exc.value)  # the password is never printed


def test_guard_refuses_a_url_the_code_under_test_does_not_use() -> None:
    other = "postgresql+psycopg://dclh:dclh@localhost:1/other_test"
    with pytest.raises(pytest.exit.Exception, match="different database"):
        ensure_safe_test_database(other)


@pytest.mark.db
def test_sites_has_rows_spots_a_populated_sites_table() -> None:
    url = opt_in_database_url()
    ensure_safe_test_database(url)
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            # A temporary table shadows any real `sites` for this connection only, so the test database is untouched.
            conn.execute(text("CREATE TEMP TABLE sites (id int)"))
            assert not sites_has_rows(conn)
            conn.execute(text("INSERT INTO sites VALUES (1)"))
            assert sites_has_rows(conn)
            conn.rollback()
    finally:
        engine.dispose()
