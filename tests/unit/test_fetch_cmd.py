"""`dclh fetch --reuse`: parse the saved snapshot without downloading it again."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import pytest
from shapely.geometry import Point
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import Settings
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source


class _Stub(Source):
    id: ClassVar[str] = "prg"
    fetched = 0

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        type(self).fetched += 1
        return store.save(self.id, b"data", suffix=".bin")

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame({"kind": ["substation"]}, geometry=[Point(0, 0)], crs="EPSG:2180")


@pytest.fixture
def settings_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setitem(cli.CONNECTORS, "prg", _Stub)
    _Stub.fetched = 0
    return settings


def test_reuse_parses_the_saved_snapshot_without_downloading(settings_in: Settings) -> None:
    store = SnapshotStore(settings_in.raw_dir)
    store.save("prg", b"data", suffix=".bin")
    result = CliRunner().invoke(cli.app, ["fetch", "prg", "--reuse"])
    assert result.exit_code == 0, result.output
    assert _Stub.fetched == 0  # nothing was downloaded
    assert "parsed 1 rows" in result.output
    assert "peak memory" in result.output
    assert store.latest_good("prg") is not None  # a parse that worked makes the snapshot a usable fallback


def test_reuse_without_a_saved_snapshot_says_so(settings_in: Settings) -> None:
    result = CliRunner().invoke(cli.app, ["fetch", "prg", "--reuse"])
    assert result.exit_code != 0
    assert "no saved snapshot" in result.output


def test_plain_fetch_still_downloads(settings_in: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(cli.app, ["fetch", "prg"])
    assert result.exit_code == 0, result.output
    assert _Stub.fetched == 1
