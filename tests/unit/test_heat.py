"""heat bonus: URE concessions are reduced at fetch time, placed by urban gmina, and an expired licence is dropped."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import ScoringConfig, SourceSpec, load_scoring
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import SiteLayers, build_site_features, heat_network_km_fact
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.heat import CONCESSIONS_URL, HeatConnector, heat_town_layer

SPEC = SourceSpec(id="heat", name="URE", used_for="t", plan="mvp", cadence="static")
SAMPLE = Path(__file__).parents[1] / "fixtures/heat/ure_concessions_sample.json"
CFG: ScoringConfig = load_scoring()


def _square(x: float, y: float, size: float = 1000.0) -> BaseGeometry:
    return box(x, y, x + size, y + size)


def _gminas() -> gpd.GeoDataFrame:
    """Two urban gminas with a concession (Rogoźno in wielkopolskie, Grodzisk Mazowiecki in mazowieckie), and
    a rural gmina with the same name as a third concession town (Kampinos), which must not be placed.
    A miejsko-wiejska seat (type 3) is not placed either: its polygon covers farmland."""
    rows = [
        ("3019011", "Rogoźno", _square(0, 0)),  # urban (type 1) in wielkopolskie
        ("1405011", "Grodzisk Mazowiecki", _square(10_000, 0)),  # urban (type 1) in mazowieckie
        ("1432032", "Kampinos", _square(20_000, 0)),  # rural (type 2): not placed
    ]
    return gpd.GeoDataFrame(
        {"teryt": [r[0] for r in rows], "name": [r[1] for r in rows]},
        geometry=[r[2] for r in rows],
        crs="EPSG:2180",
    )


def _sample() -> list[dict[str, Any]]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _saved(store: SnapshotStore) -> None:
    """The snapshot as `fetch()` stores it: the reduced rows, marked good."""
    rows = [
        {"town": r["miejscowosc"], "voivodeship": r["wojewodztwo"], "valid_to": r["dataDo"]}
        for r in _sample()
    ]
    store.mark_good(store.save("heat", json.dumps({"rows": rows}).encode(), suffix=".json"))


def test_fetch_keeps_only_the_town_voivodeship_and_end_date(
    store: SnapshotStore, make_client: Callable[..., PoliteClient]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, json=_sample())

    snapshot = HeatConnector(SPEC).fetch(make_client(handler), store)
    stored = snapshot.path.read_text(encoding="utf-8")
    for personal in ("nip", "regon", "adres", "nazwa", "0000000001", "ul. Przykładowa"):
        assert personal not in stored
    table = HeatConnector(SPEC).parse(snapshot)
    assert list(table.columns) == ["town", "voivodeship", "valid_to"]
    assert list(table["town"]) == ["Rogoźno", "Grodzisk Mazowiecki", "Kampinos"]
    assert CONCESSIONS_URL.endswith("rodzajKoncesji=PCC")


def test_fetch_refuses_an_empty_register(
    store: SnapshotStore, make_client: Callable[..., PoliteClient]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, json=[])

    with pytest.raises(ValueError, match="no concessions"):
        HeatConnector(SPEC).fetch(make_client(handler), store)


def test_towns_are_placed_by_urban_gmina_and_expired_licences_are_dropped(store: SnapshotStore) -> None:
    _saved(store)
    layer = heat_town_layer(store, SPEC, _gminas())
    assert layer is not None
    assert (
        len(layer.index) == 2
    )  # Rogoźno and Grodzisk Mazowiecki; Kampinos (rural, and expired) is not placed
    assert layer.confidence is Confidence.ESTIMATED


def test_distance_is_zero_inside_a_placed_town_and_km_outside(store: SnapshotStore) -> None:
    _saved(store)
    layer = heat_town_layer(store, SPEC, _gminas())
    inside = heat_network_km_fact(_square(100, 100, 200), layer)
    assert inside is not None and inside.value == 0.0
    near = heat_network_km_fact(box(1000 + 2000, 0, 1000 + 2100, 100), layer)  # 2 km east of Rogoźno's square
    assert near is not None and near.value == pytest.approx(2.0)
    assert near.source_id == "heat"


def test_no_snapshot_or_no_placed_town_is_missing(store: SnapshotStore) -> None:
    assert heat_town_layer(store, SPEC, _gminas()) is None  # never fetched
    assert heat_network_km_fact(_square(0, 0), None) is None
    _saved(store)
    rural_only = _gminas().iloc[[2]]  # only the rural Kampinos gmina: no concession town can be placed
    layer = heat_town_layer(store, SPEC, rural_only)
    assert layer is not None
    assert heat_network_km_fact(_square(0, 0), layer) is None


def test_build_site_features_fills_the_heat_distance(store: SnapshotStore) -> None:
    _saved(store)
    layer = heat_town_layer(store, SPEC, _gminas())
    site = Site(
        site_id="t",
        zone_id=None,
        parcel_ids=("p1",),
        geom=_square(100, 100, 200),
        area_ha=4.0,
        compactness=0.5,
        estimated_mw=24.0,
        needs_split_suggestion=False,
    )
    features = build_site_features(site, SiteLayers(heat=layer), CFG)
    assert features.dist_heat_network_km is not None and features.dist_heat_network_km.value == 0.0
    assert features.psi_aid_pts is None  # no psi layer loaded: missing, not zero
