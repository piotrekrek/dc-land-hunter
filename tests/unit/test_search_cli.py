"""`dclh search` refuses to start without its inputs; the funnel itself is in test_search.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.settings import Settings


def test_search_needs_saved_prg_and_osm_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path, contact_email="test@example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["search", "1421"])
    assert result.exit_code != 0
    assert "dclh fetch prg" in result.output


def test_search_needs_a_contact_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path, contact_email="nobody@example.invalid")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["search", "1421"])
    assert result.exit_code != 0
    assert "DCLH_CONTACT_EMAIL" in result.output
