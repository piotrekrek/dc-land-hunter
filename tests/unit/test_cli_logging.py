"""With DCLH_LOG_FILE set, the CLI also logs to that file (the workers' log survives a container recreate)."""

from __future__ import annotations

import logging
import logging.handlers
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dclh_pipeline import cli


@pytest.fixture
def root_handlers() -> Iterator[None]:
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


@pytest.mark.usefixtures("root_handlers")
def test_log_file_gets_the_log_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = tmp_path / "logs" / "worker.log"  # the folder does not exist yet
    monkeypatch.setenv("DCLH_LOG_FILE", str(log_file))
    assert CliRunner().invoke(cli.app, ["version"]).exit_code == 0
    logging.getLogger("dclh_pipeline.jobs").warning("job 7 failed")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "WARNING dclh_pipeline.jobs: job 7 failed" in log_file.read_text()


@pytest.mark.usefixtures("root_handlers")
def test_no_log_file_without_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCLH_LOG_FILE", raising=False)
    assert CliRunner().invoke(cli.app, ["version"]).exit_code == 0
    files = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert files == []
