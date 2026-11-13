"""`dclh reprofile` against a real PostGIS database: the gmina's plan ogólny comes from the latest act list."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.db import session_scope
from dclh_pipeline.scoring.run import score_candidates, write_sites
from dclh_pipeline.snapshots import SnapshotStore
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

ACTS = [{"teryt": "080504", "status": "legalForce", "title": "Plan ogólny gminy Rzepin", "in_force_from": ""}]


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    yield
    with session_scope() as session:  # the next module refuses a database whose sites table has rows
        session.execute(text("DELETE FROM sites"))


def zoning_checks() -> dict[str, str]:
    """parcel id → the built profile's zoning item, for every stored site."""
    with session_scope() as session:
        rows = session.execute(
            text("SELECT parcel_ids[1], profiles -> 'built' -> 'to_check' FROM sites")
        ).all()
    return {pid: next(c["text_pl"] for c in checks if c["key"] == "zoning") for pid, checks in rows}


def test_reprofile_reads_the_gmina_plan_from_the_latest_act_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope() as session:
        write_sites(
            session,
            score_candidates(parcel_sites(), layers(), CFG),
            scored_at=datetime(2026, 11, 13, tzinfo=UTC),
            powiat="0805",
        )
        # No plan was found for any site; two lie in gminas the act list can tell apart.
        session.execute(text("UPDATE sites SET detail = jsonb_set(detail, '{features,zoning}', 'null')"))
        session.execute(
            text(
                "UPDATE sites SET parcel_ids = ARRAY[CASE parcel_ids[1] WHEN 'a1' THEN '080504_5.0010.85/8' "
                "WHEN 'c1' THEN '142105_2.0007.33' ELSE parcel_ids[1] END]"
            )
        )
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(raw_dir=tmp_path))

    assert CliRunner().invoke(cli.app, ["reprofile"]).exit_code == 0
    assert all(t.startswith("Brak planu miejscowego w rejestrze: sprawdź") for t in zoning_checks().values())

    store = SnapshotStore(tmp_path)
    store.mark_good(store.save("ru_acts", json.dumps(ACTS).encode(), suffix=".json"))
    result = CliRunner().invoke(cli.app, ["reprofile"])
    assert result.exit_code == 0, result.output
    checks = zoning_checks()
    assert checks["080504_5.0010.85/8"].startswith("Gmina ma plan ogólny")
    assert "gmina nie ma planu ogólnego" in checks["142105_2.0007.33"]
    assert checks["b1"].startswith("Brak planu miejscowego w rejestrze: sprawdź")  # no gmina code in the id
    with session_scope() as session:  # the stored features keep what the search saw
        stamped = session.execute(
            text("SELECT count(*) FROM sites WHERE detail -> 'features' ->> 'gmina_plan_ogolny' IS NOT NULL")
        ).scalar_one()
    assert stamped == 0
