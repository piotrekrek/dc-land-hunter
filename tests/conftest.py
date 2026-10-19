from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore

FIXTURES = Path(__file__).parent / "fixtures"
ALEMBIC_INI = Path(__file__).parents[1] / "api" / "alembic.ini"

# Database tests wipe the schema, so they run only against an explicitly named test database, never DATABASE_URL
# (which on the VPS and in a dev `.env` points at real data). The opt-in URL also becomes DATABASE_URL for the
# code under test; a process env var beats `.env` in pydantic-settings.
TEST_DB_ENV = "DCLH_TEST_DATABASE_URL"
if os.environ.get(TEST_DB_ENV):
    os.environ["DATABASE_URL"] = os.environ[TEST_DB_ENV]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Say loudly when the database tests were skipped: auth, db, sites_db and grid_load are untested without it."""
    skipped = [
        r
        for r in terminalreporter.stats.get("skipped", [])
        if any(m in str(getattr(r, "longrepr", "")) for m in ("no database at", "no test database"))
    ]
    if skipped:
        terminalreporter.write_sep(
            "!",
            f"{len(skipped)} database tests SKIPPED (no PostGIS at {TEST_DB_ENV}); CI runs them",
            yellow=True,
        )


def sites_has_rows(conn: Connection) -> bool:
    """True when a `sites` table is visible and holds at least one row: the sign of a real database."""
    if conn.execute(text("SELECT to_regclass('sites')")).scalar_one() is None:
        return False
    return bool(conn.execute(text("SELECT EXISTS (SELECT 1 FROM sites)")).scalar_one())


def ensure_safe_test_database(url: str) -> None:
    """Abort the run unless `url` is a throwaway test database; skip when it is unreachable.

    Rule: the database name must end in `_test`, the code under test must use the same URL, and the database
    must not hold any scored sites.
    """
    parsed = make_url(url)
    shown = parsed.render_as_string(hide_password=True)
    if not (parsed.database or "").endswith("_test"):
        pytest.exit(
            f"refusing to wipe {shown}: {TEST_DB_ENV} must name a database ending in _test", returncode=3
        )
    if get_settings().database_url != url:
        pytest.exit(
            f"refusing to run: settings point at a different database than {TEST_DB_ENV}", returncode=3
        )
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            populated = sites_has_rows(conn)
    except OperationalError:
        pytest.skip(f"no database at {shown}")
    finally:
        engine.dispose()
    if populated:
        pytest.exit(
            f"refusing to wipe {shown}: its sites table has rows, so it is not a scratch database",
            returncode=3,
        )


def opt_in_database_url() -> str:
    """The test database URL from the opt-in variable; skips the test when it is unset."""
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        pytest.skip(f"no test database: set {TEST_DB_ENV} to a PostGIS database whose name ends in _test")
    return url


@pytest.fixture(scope="module")
def migrated_db() -> Iterator[None]:
    """A freshly migrated, empty schema in the opt-in test database, once per test module."""
    ensure_safe_test_database(opt_in_database_url())
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "raw")


@pytest.fixture
def make_client(fake_clock: FakeClock) -> Callable[..., PoliteClient]:
    def factory(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> PoliteClient:
        return PoliteClient(
            user_agent="DCLandHunter/test (+mailto:test@example.org)",
            transport=httpx.MockTransport(handler),
            clock=fake_clock,
            sleep=fake_clock.sleep,
            **kwargs,  # type: ignore[arg-type]
        )

    return factory
