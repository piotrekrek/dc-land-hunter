"""The check worker holds the static layers while it is busy and gives them back when it is idle."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from dclh_pipeline import check_job, jobs, layers
from dclh_pipeline.cli import app


@pytest.fixture
def cached(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    cache: dict[str, Any] = {"key": ("k",), "layers": object()}
    monkeypatch.setattr(layers, "_STATIC_CACHE", cache)
    return cache


def test_release_drops_the_cache_and_says_it_did(cached: dict[str, Any]) -> None:
    assert layers.release_static_layers() is True
    assert cached["layers"] is None and cached["key"] is None
    assert layers.release_static_layers() is False  # nothing left to drop


def test_the_hook_releases_only_after_the_idle_time(cached: dict[str, Any]) -> None:
    check_job.release_when_idle(check_job.STATIC_IDLE_S - 1)
    assert cached["layers"] is not None
    check_job.release_when_idle(check_job.STATIC_IDLE_S)
    assert cached["layers"] is None


def test_the_check_worker_gets_the_hook_and_the_general_worker_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(jobs, "work", lambda **kwargs: seen.append(kwargs) or 0)
    runner = CliRunner()
    assert runner.invoke(app, ["worker", "--once", "--kinds", "check_parcel"]).exit_code == 0
    assert runner.invoke(app, ["worker", "--once", "--exclude-kinds", "check_parcel"]).exit_code == 0
    assert seen[0]["on_idle"] is check_job.release_when_idle
    assert seen[1]["on_idle"] is None  # the general worker's layers are rebuilt per search anyway
