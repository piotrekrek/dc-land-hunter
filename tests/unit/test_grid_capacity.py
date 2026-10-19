"""Loading the Phase 3 layers from saved snapshots (`grid_capacity.py`) and placing PSE stations by name."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, box

from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.grid_capacity import load_grid_capacity
from dclh_pipeline.model.capacity import build_gpz_capacity_layer, locate_points
from dclh_pipeline.model.features import Substation, published_headroom_fact
from dclh_pipeline.model.gpz_match import GpzCandidate
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.manual import EnergaCapacity, PseClosedPoints

CFG = load_scoring()
SOURCES = load_sources()
LIVE = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19"
TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


def _tauron_snapshot(store: SnapshotStore) -> None:
    page = json.loads((LIVE / "tauron_gpz_search.json").read_text(encoding="utf-8"))
    page["data"]["total"] = len(page["data"]["matches"])
    snapshot = store.save("tauron", json.dumps({"year": 2026, "pages": [page]}).encode(), suffix=".json")
    store.mark_good(snapshot)


def _substations() -> list[Substation]:
    return [
        Substation(
            1,
            SubstationClass.GPZ_110_SN,
            (110_000, 15_000),
            Point(500_000, 700_000),
            "GPZ Białogard",
            "Energa",
        ),
        Substation(
            2, SubstationClass.NODE_220_400, (220_000, 110_000), Point(520_000, 690_000), "SE Aniołów", "PSE"
        ),
        Substation(
            3, SubstationClass.NODE_220_400, (400_000,), Point(400_000, 400_000), "Stacja 400kV „SE Wrocław”"
        ),
    ]


def test_nothing_saved_leaves_every_layer_unloaded(tmp_path: Path) -> None:
    loaded = load_grid_capacity(SnapshotStore(tmp_path / "raw"), SOURCES, _substations(), CFG)
    assert (loaded.capacity, loaded.pse_closed, loaded.pse_odb, loaded.pse_planned) == (
        None,
        None,
        None,
        None,
    )
    assert loaded.results == []


def test_snapshots_of_each_kind_become_layers(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "raw")
    _tauron_snapshot(store)
    energa = tmp_path / "energa.csv"
    energa.write_text(
        "dso,gpz_name,published_mw,as_of\nenerga,Białogard,40,2026-08-31\nenerga,Nigdzie,5,2026-08-31\n"
    )
    EnergaCapacity(SOURCES.get("energa"), energa).import_file(store)
    PseClosedPoints(SOURCES.get("pse_closed"), LIVE / "pse_closed_20260204.xlsx").import_file(store)

    loaded = load_grid_capacity(store, SOURCES, _substations(), CFG, mapping_path=tmp_path / "none.csv")

    assert sorted(loaded.snapshots) == ["energa", "pse_closed", "tauron"]
    assert loaded.capacity is not None
    by_source = {(r.dso, r.dso_gpz_name): r for r in loaded.results}
    assert by_source[("energa", "Białogard")].candidate_key == 1
    assert by_source[("energa", "Nigdzie")].method == "none"
    assert by_source[("tauron", "Bierdzany")].method == "published"
    assert by_source[("pse_closed", "Aniołów")].candidate_key == 2
    assert by_source[("pse_closed", "Wrocław")].candidate_key == 3
    assert by_source[("pse_closed", "Bieruń")].method == "none"  # not in this OSM sample
    assert loaded.pse_closed is not None and len(loaded.pse_closed.index) == 2  # Aniołów, Wrocław (400 kV)
    assert loaded.pse_odb is None  # never imported: stays missing


def test_published_location_is_used_as_it_is_at_verified_confidence() -> None:
    x, y = TO_2180.transform(18.1727133, 50.8130818)
    capacity = pd.DataFrame(
        {
            "dso": ["tauron", "energa"],
            "gpz_name": ["Bierdzany", "Białogard"],
            "published_mw": [2.2, 40.0],
            "as_of": [pd.Timestamp("2026-10-19"), pd.Timestamp("2026-08-31")],
            "x_2180": [x, None],
            "y_2180": [y, None],
        }
    )
    candidates = [GpzCandidate(1, "GPZ Białogard", Point(500_000, 700_000))]
    layer, results = build_gpz_capacity_layer(capacity, candidates, min_score=90)
    assert [(r.dso, r.method) for r in results] == [("tauron", "published"), ("energa", "fuzzy")]
    site = box(x - 100, y - 100, x + 100, y + 100)
    fact = published_headroom_fact(site, layer, CFG)
    assert fact is not None
    assert (fact.value, fact.source_id, fact.confidence) == (2.2, "tauron", Confidence.VERIFIED)


def test_locate_points_keeps_given_coordinates_and_drops_unknown_names() -> None:
    points = gpd.GeoDataFrame(
        {"name": ["Aniołów", "Aniołów", "Ręczna", "Nieznana"], "as_of": [pd.Timestamp("2026-02-04")] * 4},
        geometry=[None, None, Point(1, 2), None],
        crs="EPSG:2180",
    )
    nodes = [GpzCandidate(2, "SE Aniołów", Point(520_000, 690_000))]
    located, results = locate_points(points, "pse_closed", nodes)
    assert list(located["name"]) == ["Aniołów", "Aniołów", "Ręczna"]
    assert located.geometry.iloc[0] == Point(520_000, 690_000)
    assert located.geometry.iloc[2] == Point(1, 2)
    assert sorted((r.dso_gpz_name, r.method) for r in results) == [("Aniołów", "fuzzy"), ("Nieznana", "none")]
