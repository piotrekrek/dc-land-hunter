"""OSM power connector on a tiny generated PBF: no network, no 2 GB extract."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import osmium
import pandas as pd
import pytest
from osmium.osm.mutable import Node, Way

from dclh_pipeline.config import load_sources
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.capacity import build_gpz_capacity_layer
from dclh_pipeline.model.features import build_grid_index
from dclh_pipeline.model.gpz_match import gpz_candidates
from dclh_pipeline.model.grid import WAY_ID_OFFSET, SubstationClass, osm_ref, parse_osm_ref
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.osm_power import OsmPowerConnector, to_grid

ClientFactory = Callable[..., PoliteClient]


def _spec():  # type: ignore[no-untyped-def]
    return next(s for s in load_sources().sources if s.id == "osm_power")


def _write_pbf(path: Path) -> Path:
    with osmium.SimpleWriter(str(path)) as w:
        w.add_node(
            Node(
                id=1,
                location=(16.90, 52.40),
                tags={
                    "power": "substation",
                    "name": "GPZ Piaski",
                    "voltage": "110000;15000",
                    "operator": "Enea Operator",
                },
            )
        )
        for node_id, lon, lat in [(2, 16.95, 52.40), (3, 16.95, 52.45), (4, 16.90, 52.45)]:
            w.add_node(Node(id=node_id, location=(lon, lat)))
        w.add_node(Node(id=5, location=(17.0, 52.5), tags={"amenity": "cafe"}))
        w.add_way(Way(id=10, nodes=[2, 3, 4, 2], tags={"power": "substation", "name": "GPZ Żabno"}))
        w.add_way(Way(id=11, nodes=[2, 3], tags={"power": "line", "voltage": "110000"}))
        w.add_way(Way(id=12, nodes=[3, 4], tags={"power": "minor_line", "voltage": "15000"}))
    return path


@pytest.fixture
def pbf(tmp_path: Path) -> Path:
    return _write_pbf(tmp_path / "mini.osm.pbf")


def _snapshot(path: Path) -> Snapshot:
    return Snapshot(
        source_id="osm_power",
        fetched_at=pd.Timestamp("2026-10-11", tz="UTC").to_pydatetime(),
        sha256="0" * 64,
        path=path,
        size_bytes=path.stat().st_size,
    )


def _connector(**kwargs: object) -> OsmPowerConnector:
    return OsmPowerConnector(_spec(), min_substations=kwargs.pop("min_substations", 2), **kwargs)  # type: ignore[arg-type]


def test_parse_reads_substations_and_lines_and_skips_the_rest(pbf: Path) -> None:
    table = _connector().parse(_snapshot(pbf))
    assert sorted(table["kind"]) == ["line", "substation", "substation"]  # no cafe, no minor_line
    by_name = table[table["kind"] == "substation"].set_index("name")
    assert by_name.loc["GPZ Piaski", "osm_id"] == 1  # a node keeps its id
    assert by_name.loc["GPZ Żabno", "osm_id"] == WAY_ID_OFFSET + 10  # a way gets the offset
    assert by_name.loc["GPZ Piaski", "operator"] == "Enea Operator"
    assert table.crs.to_epsg() == 2180
    point = by_name.loc["GPZ Piaski", "geometry"]
    assert abs(point.x - 358_000) < 20_000 and abs(point.y - 505_000) < 20_000  # near Poznań


def test_to_grid_classifies_by_voltage_and_keeps_names(pbf: Path) -> None:
    substations, lines = to_grid(_connector().parse(_snapshot(pbf)))
    by_name = {s.name: s for s in substations}
    assert by_name["GPZ Piaski"].cls is SubstationClass.GPZ_110_SN
    assert by_name["GPZ Piaski"].voltages == (110_000, 15_000)
    assert by_name["GPZ Żabno"].cls is SubstationClass.UNKNOWN_VOLTAGE  # no voltage tag: kept, never dropped
    assert [ln.voltage for ln in lines] == [110_000]


def test_operator_reaches_the_grid_so_utility_stations_are_not_own_substations(pbf: Path) -> None:
    substations, lines = to_grid(_connector().parse(_snapshot(pbf)))
    assert {s.name: s.operator for s in substations} == {"GPZ Piaski": "Enea Operator", "GPZ Żabno": None}
    grid = build_grid_index(substations, lines)
    assert len(grid.own_hv110) == 0  # an Enea GPZ is not a factory's own substation


def test_extract_with_too_few_substations_is_rejected(pbf: Path) -> None:
    with pytest.raises(ValueError, match="expected at least 1000"):
        OsmPowerConnector(_spec()).parse(_snapshot(pbf))


def test_substations_outside_poland_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "far.osm.pbf"
    with osmium.SimpleWriter(str(path)) as w:
        w.add_node(Node(id=1, location=(2.35, 48.85), tags={"power": "substation", "voltage": "110000"}))
    with pytest.raises(ValueError, match="outside Poland"):
        _connector(min_substations=1).parse(_snapshot(path))


def test_osm_names_feed_the_dso_matching_end_to_end(pbf: Path) -> None:
    substations, _ = to_grid(_connector().parse(_snapshot(pbf)))
    capacity = pd.DataFrame(
        {
            "dso": ["enea"],
            "gpz_name": ["Piaski"],
            "published_mw": [12.5],
            "as_of": [pd.Timestamp("2026-09-01")],
            "note": [""],
        }
    )
    layer, results = build_gpz_capacity_layer(capacity, gpz_candidates(substations))
    assert results[0].candidate_key == 1
    assert layer.items[0].mw == 12.5


def test_fetch_streams_the_extract_into_a_snapshot(
    pbf: Path, tmp_path: Path, make_client: ClientFactory
) -> None:
    body = pbf.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "application/octet-stream"})

    store = SnapshotStore(tmp_path / "raw")
    snapshot = _connector().fetch(make_client(handler), store)
    assert snapshot.path.name.endswith(".osm.pbf")
    assert snapshot.path.read_bytes() == body
    assert len(_connector().parse(snapshot)) == 3


def test_a_cut_off_download_never_becomes_a_snapshot(
    pbf: Path, tmp_path: Path, make_client: ClientFactory
) -> None:
    body = pbf.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-length": str(len(body) + 500)})

    store = SnapshotStore(tmp_path / "raw")
    with pytest.raises(ValueError, match="server announced"):
        _connector().fetch(make_client(handler), store)
    assert store.latest("osm_power") is None


def test_osm_refs_round_trip_and_never_collide() -> None:
    assert osm_ref(5) == "n5"
    assert osm_ref(WAY_ID_OFFSET + 5) == "w5"
    assert parse_osm_ref("5") == parse_osm_ref("n5") == 5
    assert parse_osm_ref("W5") == WAY_ID_OFFSET + 5
    with pytest.raises(ValueError, match="not an OSM reference"):
        parse_osm_ref("r5")
