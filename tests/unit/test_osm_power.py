"""OSM power connector on a tiny generated PBF: no network, no 2 GB extract."""

from __future__ import annotations

import shutil
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
from dclh_pipeline.sources.osm_power import OsmPowerConnector, _NodeLocations, to_grid

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


def _write_many_nodes_pbf(path: Path, *, drop_node: int | None = None) -> Path:
    """5,000 unrelated nodes plus one power line through nodes 9001, 9002, 9003 (west to east)."""
    with osmium.SimpleWriter(str(path)) as w:
        for node_id in range(1, 5001):
            w.add_node(Node(id=node_id, location=(14.0 + node_id / 1000, 50.0)))
        for node_id, lon in [(9001, 17.0), (9002, 17.1), (9003, 17.2)]:
            if node_id != drop_node:
                w.add_node(Node(id=node_id, location=(lon, 52.0)))
        w.add_node(
            Node(id=9100, location=(17.0, 52.1), tags={"power": "substation", "voltage": "110000;15000"})
        )
        w.add_way(Way(id=50, nodes=[9001, 9002, 9003], tags={"power": "line", "voltage": "110000"}))
    return path


def test_only_the_nodes_that_power_ways_use_are_kept_in_memory(tmp_path: Path) -> None:
    path = _write_many_nodes_pbf(tmp_path / "many.osm.pbf")
    locations = _NodeLocations.read(str(path), [9001, 9002, 9003, 9003])
    assert list(locations.ids) == [9001, 9002, 9003]  # 5,000 other nodes were not decoded or stored


def test_line_geometry_follows_the_node_order(tmp_path: Path) -> None:
    path = _write_many_nodes_pbf(tmp_path / "many.osm.pbf")
    table = _connector(min_substations=1).parse(_snapshot(path))
    line = table[table["kind"] == "line"].geometry.iloc[0]
    xs = [x for x, _ in line.coords]
    assert xs == sorted(xs)  # west to east, as the nodes were listed
    assert len(xs) == 3
    assert table[table["kind"] == "substation"]["osm_id"].tolist() == [9100]


def test_a_way_with_a_node_missing_from_the_extract_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = _write_many_nodes_pbf(tmp_path / "cut.osm.pbf", drop_node=9002)  # cut at the border
    table = _connector(min_substations=1).parse(_snapshot(path))
    assert "line" not in set(table["kind"])
    assert len(table) == 1  # the substation node is still read


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool not installed")
def test_prefilter_gives_the_same_grid_as_the_full_extract(pbf: Path, tmp_path: Path) -> None:
    """The power-only cut must not change what the parser returns, only how much it reads."""
    from dclh_pipeline.sources.osm_power import prefilter_power

    cut = prefilter_power(pbf, tmp_path / "power.osm.pbf")
    full = _connector().parse(_snapshot(pbf))
    small = _connector().parse(_snapshot(cut))
    assert cut.stat().st_size <= pbf.stat().st_size
    assert sorted(full["name"]) == sorted(small["name"])
    assert len(full) == len(small)
