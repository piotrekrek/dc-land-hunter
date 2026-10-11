"""`dclh import-manual` end to end: a CSV is stored as a snapshot and validated; bad files fail loudly."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.settings import Settings
from dclh_pipeline.snapshots import SnapshotStore


@pytest.fixture
def settings_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_import_manual_stores_and_validates_the_file(tmp_path: Path, settings_in: Settings) -> None:
    file = tmp_path / "enea_mapa.csv"
    file.write_text(
        "dso,gpz_name,published_mw,as_of,note\nenea,GPZ Piaski,12,2026-09-01,\n", encoding="utf-8"
    )
    result = CliRunner().invoke(cli.app, ["import-manual", "enea", str(file)])
    assert result.exit_code == 0, result.output
    assert "validated 1 rows" in result.output
    assert file.exists()  # the owner's file is not moved
    store = SnapshotStore(settings_in.raw_dir)
    assert store.latest_good("enea") is not None  # an accepted file can serve as the fallback snapshot


def test_import_manual_reports_the_row_of_a_bad_value(tmp_path: Path, settings_in: Settings) -> None:
    file = tmp_path / "enea.csv"
    file.write_text("dso,gpz_name,published_mw,as_of\nenea,Piaski,-3,2026-09-01\n", encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["import-manual", "enea", str(file)])
    assert result.exit_code != 0
    assert "row 2" in result.output
    assert "was not imported" in result.output
    assert not (settings_in.raw_dir / "enea").exists()  # a rejected file leaves no trace in the store


def test_import_manual_rejects_unknown_sources_and_missing_files(
    tmp_path: Path, settings_in: Settings
) -> None:
    result = CliRunner().invoke(cli.app, ["import-manual", "prg", str(tmp_path / "x.csv")])
    assert result.exit_code != 0
    assert "no manual import" in result.output
    result = CliRunner().invoke(cli.app, ["import-manual", "enea", str(tmp_path / "missing.csv")])
    assert result.exit_code != 0
    assert "no such file" in result.output
