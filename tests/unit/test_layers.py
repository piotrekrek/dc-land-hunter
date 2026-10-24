"""Area layers shared by the search and a parcel check: a check survives a failed layer, a search does not."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import box

from dclh_pipeline import layers
from dclh_pipeline.layers import AreaLayers, StaticLayers, build_site_layers, load_area_layers

NOW = datetime(2026, 10, 24, tzinfo=UTC)
POWIAT = box(0, 0, 10_000, 10_000)
BBOX = (4_000.0, 4_000.0, 5_000.0, 5_000.0)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, bool | None]] = []
        self.errors: dict[str, str | None] = {}
        self.results: dict[str, Any] = {}

    def start(self, key: str) -> None:
        self.events.append(("start", key, None))

    def finish(self, key: str, *, ok: bool, result: Any = None, error: str | None = None) -> None:
        self.events.append(("finish", key, ok))
        self.errors[key] = error
        self.results[key] = result


class FakeStore:
    def mark_good(self, snapshot: object) -> None:
        pass


def frame(geoms: list[Any], **columns: list[Any]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns, geometry=geoms, crs="EPSG:2180")


def fake_connector(parsed: Any = None, *, fail: str | None = None, forest: Any = None) -> type:
    class Connector:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def fetch(self, client: object, store: object) -> SimpleNamespace:
            if fail:
                raise RuntimeError(fail)
            return SimpleNamespace(fetched_at=NOW)

        def parse(self, snapshot: object) -> Any:
            return parsed

        def parse_forest(self, snapshot: object) -> Any:
            return forest

    return Connector


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    flood = frame([box(4_100, 4_100, 4_200, 4_200)], probability=[1])
    monkeypatch.setattr(layers, "IsokConnector", fake_connector(flood))
    monkeypatch.setattr(layers, "zones_with_probability", lambda data, p: data.geometry)
    monkeypatch.setattr(
        layers, "MidasConnector", fake_connector(frame([box(0, 0, 1, 1)], kind=["mining_area"]))
    )
    houses = frame([box(4_300, 4_300, 4_310, 4_310), box(9_000, 9_000, 9_010, 9_010)])
    trees = frame([box(4_400, 4_400, 4_450, 4_450)])
    monkeypatch.setattr(layers, "Bdot10kConnector", fake_connector(houses, forest=trees))
    monkeypatch.setattr(layers, "ru_zoning_layers", lambda *a, **k: ())
    return monkeypatch


def load(**kwargs: Any) -> tuple[AreaLayers, Recorder]:
    recorder = Recorder()
    config = SimpleNamespace(get=lambda name: name)
    area = load_area_layers(
        object(),
        config,
        FakeStore(),
        BBOX,
        "1421",
        powiat_area=POWIAT,
        reporter=recorder,
        **kwargs,  # type: ignore[arg-type]
    )
    return area, recorder


def test_every_step_is_reported_in_order_with_its_result(patched: pytest.MonkeyPatch) -> None:
    area, recorder = load(strict=False)
    assert [e[1] for e in recorder.events if e[0] == "start"] == ["flood", "mining", "bdot10k", "zoning"]
    assert all(e[2] for e in recorder.events if e[0] == "finish")
    assert set(recorder.results["flood"]) == {"flood_10_or_1", "flood_0_2"}
    assert area.flood is not None and area.housing is not None and area.forest is not None


def test_a_failed_layer_stays_missing_in_a_check_and_the_rest_still_load(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(layers, "IsokConnector", fake_connector(fail="504 Gateway Timeout"))
    area, recorder = load(strict=False)
    assert area.flood is None and area.flood_02 is None  # never an empty layer that would read as "no flood"
    assert area.housing is not None and area.mining is not None
    assert ("finish", "flood", False) in recorder.events
    assert "504 Gateway Timeout" in (recorder.errors["flood"] or "")


def test_the_same_failure_fails_a_search(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(layers, "IsokConnector", fake_connector(fail="504 Gateway Timeout"))
    with pytest.raises(RuntimeError, match="504"):
        load(strict=True)


def test_buildings_outside_the_powiat_fail_the_housing_step(patched: pytest.MonkeyPatch) -> None:
    far = frame([box(50_000, 50_000, 50_010, 50_010)])
    patched.setattr(layers, "Bdot10kConnector", fake_connector(far, forest=far))
    area, recorder = load(strict=False)
    assert area.housing is None and area.forest is None
    assert "no residential building" in (recorder.errors["bdot10k"] or "")


def test_clip_cuts_buildings_and_forest_to_the_window(patched: pytest.MonkeyPatch) -> None:
    area, _ = load(strict=False, clip=BBOX)
    assert area.housing is not None and len(area.housing.index) == 1  # the house at 9 km is cut away
    whole, _ = load(strict=False)
    assert whole.housing is not None and len(whole.housing.index) == 2


def static_stub() -> StaticLayers:
    layer = SimpleNamespace(source_id="x")
    return SimpleNamespace(  # type: ignore[return-value]
        protected=layer,
        landscape=layer,
        gas=layer,
        ols=layer,
        grid="grid",
        heat=None,
        psi=None,
        big_cities=layer,
        res_clusters=layer,
        capacity=SimpleNamespace(capacity="cap", pse_closed=None, pse_odb=None, pse_planned=None),
    )


def test_site_layers_leave_out_what_did_not_load(patched: pytest.MonkeyPatch) -> None:
    area, _ = load(strict=False)
    full = build_site_layers(static_stub(), area, None)
    assert {"flood_10_or_1", "flood_0_2", "forest", "protected_nature", "mining_area"} <= set(
        full.constraints
    )

    patched.setattr(layers, "IsokConnector", fake_connector(fail="down"))
    partial_area, _ = load(strict=False)
    partial = build_site_layers(static_stub(), partial_area, None)
    assert "flood_10_or_1" not in partial.constraints and "flood_0_2" not in partial.constraints
    assert "forest" in partial.constraints and partial.residential is not None
