"""Migration 0009 moves the three starter priority powiats from the crontab into the saved list."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from dclh_pipeline.db import session_scope

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"
STARTERS = {"0805", "3021", "1465"}


def seeded() -> set[str]:
    with session_scope() as session:
        rows = session.execute(text("SELECT teryt FROM powiat_priority WHERE added_by = 'seed'")).scalars()
        return set(rows)


def test_the_starter_powiats_are_on_the_saved_list_after_upgrade() -> None:
    assert seeded() == STARTERS


def test_downgrade_removes_only_the_seed_rows_and_upgrade_restores_them() -> None:
    with session_scope() as session:
        session.execute(
            text("INSERT INTO powiat_priority (teryt, added_by) VALUES ('1421', 'ala@example.org')")
        )
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0008")
    assert seeded() == set()
    with session_scope() as session:
        assert session.execute(text("SELECT teryt FROM powiat_priority")).scalars().all() == ["1421"]
    command.upgrade(cfg, "head")
    assert seeded() == STARTERS
    with session_scope() as session:
        session.execute(text("DELETE FROM powiat_priority WHERE teryt = '1421'"))
