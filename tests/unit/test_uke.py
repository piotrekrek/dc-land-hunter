"""uke: SIIS fibre nodes are reduced from the archive's CSV views, dated 2021, and give `dist_telecom_node_km`."""

from __future__ import annotations

import csv
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pyproj import Transformer
from shapely.geometry import Point, box

from dclh_pipeline.config import ScoringConfig, SourceSpec, load_scoring
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import SiteLayers, build_site_features, telecom_node_km_fact
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources import uke
from dclh_pipeline.sources.uke import DATA_AS_OF, UkeConnector, reduce_nodes, telecom_node_layer

SPEC = SourceSpec(id="uke", name="UKE", used_for="t", plan="mvp", cadence="static")
FIXTURES = Path(__file__).parents[1] / "fixtures/uke"
NODES = FIXTURES / "infrastructure_node_view_sample.csv"
INTERFACES = FIXTURES / "infrastructure_nodeinterface_view_sample.csv"
CFG: ScoringConfig = load_scoring()
TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
# Kąty Wrocławskie, site-95bad491aa77
SITE_LON, SITE_LAT = 16.8854, 51.0424


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(content.decode("utf-8").splitlines()))


def _with_extra_node(tmp_path: Path, **node: str) -> tuple[Path, Path]:
    """The fixture plus one fibre node, for the cases the real sample lacks."""
    nodes, interfaces = tmp_path / "n.csv", tmp_path / "i.csv"
    shutil.copy(NODES, nodes)
    shutil.copy(INTERFACES, interfaces)
    with NODES.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    row = dict.fromkeys(header, "") | {"owner_type": "Węzeł własny"} | node
    with nodes.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, header).writerow(row)
    with interfaces.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(
            ["1", "i", row["users_entity_id"], "", row["id"], "", "światłowodowe", "GPON", "", "", ""]
        )
    return nodes, interfaces


def _saved(store: SnapshotStore) -> None:
    store.mark_good(
        store.save("uke", reduce_nodes(NODES, INTERFACES, min_nodes=1), suffix=".csv", compress=True)
    )


def _site(geom: object) -> Site:
    return Site(
        site_id="t", zone_id=None, parcel_ids=("p1",), geom=geom, area_ha=10.0, compactness=0.5,  # type: ignore[arg-type]
        estimated_mw=60.0, needs_split_suggestion=False,
    )  # fmt: skip


def test_only_fibre_nodes_with_coordinates_are_kept() -> None:
    rows = _rows(reduce_nodes(NODES, INTERFACES, min_nodes=1))
    assert [r["node_id"] for r in rows] == ["28510256", "29163445", "29166817", "29166818"]
    assert {r["operator"] for r in rows} == {"Orange Polska S.A.", "NETIA S.A."}
    assert rows[0]["fibre_tech"] == "1 Gigabit Ethernet"
    assert all(r["shared"] in {"0", "1"} for r in rows)


def test_a_sole_traders_name_is_not_kept(tmp_path: Path) -> None:
    nodes, interfaces = _with_extra_node(
        tmp_path, id="1", users_entity_id="77", users_entity_id_name="Jan Przykładowy Usługi Internetowe",
        latitude="51.1", longitude="17.0",
    )  # fmt: skip
    content = reduce_nodes(nodes, interfaces, min_nodes=1)
    row = next(r for r in _rows(content) if r["node_id"] == "1")
    assert row["operator"] == "" and row["operator_id"] == "77"
    assert "Przykładowy" not in content.decode("utf-8")


def test_a_node_outside_poland_fails_loudly(tmp_path: Path) -> None:
    nodes, interfaces = _with_extra_node(
        tmp_path,
        id="2",
        users_entity_id="5",
        latitude="52.52",
        longitude="13.40",  # Berlin
    )
    with pytest.raises(ValueError, match="outside Poland"):
        reduce_nodes(nodes, interfaces, min_nodes=1)


def test_too_few_nodes_is_not_saved() -> None:
    with pytest.raises(ValueError, match="only 4 fibre nodes"):
        reduce_nodes(NODES, INTERFACES)  # the default minimum is the national scale


def test_fetch_downloads_extracts_and_saves_the_reduced_nodes(
    store: SnapshotStore, make_client: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"Rar!fake")

    def fake_extract(archive: Path, member: str, dest: Path) -> None:
        assert archive.read_bytes() == b"Rar!fake"
        shutil.copy(NODES if member == uke.NODES_MEMBER else INTERFACES, dest)

    monkeypatch.setattr(uke, "extract_member", fake_extract)
    snapshot = UkeConnector(SPEC, min_nodes=1).fetch(make_client(handler), store)
    assert snapshot.path.name.endswith(".csv.gz")
    table = UkeConnector(SPEC).parse(snapshot)
    assert len(table) == 4
    assert table["x"].between(171_000, 861_000).all() and table["y"].between(133_000, 775_000).all()


def test_layer_is_dated_by_the_archive_not_the_download(store: SnapshotStore) -> None:
    assert telecom_node_layer(store, SPEC) is None  # never fetched
    _saved(store)
    layer = telecom_node_layer(store, SPEC)
    assert layer is not None and len(layer.index) == 4
    assert layer.source_id == "uke"
    assert layer.as_of == DATA_AS_OF
    assert layer.confidence is Confidence.ESTIMATED


def test_missing_layer_leaves_the_fact_missing() -> None:
    assert telecom_node_km_fact(box(0, 0, 10, 10), None) is None
    features = build_site_features(_site(box(0, 0, 10, 10)), SiteLayers(), CFG)
    assert features.dist_telecom_node_km is None


def test_a_site_in_kąty_wrocławskie_is_inside_the_2_km_band(store: SnapshotStore) -> None:
    _saved(store)
    layer = telecom_node_layer(store, SPEC)
    x, y = TO_2180.transform(SITE_LON, SITE_LAT)
    site = _site(Point(x, y).buffer(150))
    features = build_site_features(site, SiteLayers(telecom=layer), CFG)
    fact = features.dist_telecom_node_km
    assert fact is not None and fact.source_id == "uke" and fact.confidence is Confidence.ESTIMATED
    assert 0 < fact.value < 2.0  # the nearest fibre node in the sample is NETIA's, about 1 km east
    item = next(i for i in score_site(features, CFG).items if i.key == "conn_telecom")
    assert item.points == item.max_points
