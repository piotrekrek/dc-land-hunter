from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.midas import LAYERS, MidasConnector, _read_layer

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="midas", name="MIDAS", used_for="t", plan="mvp", cadence="monthly")
LIVE_DIR = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19"
BBOX = (620_000.0, 470_000.0, 630_000.0, 480_000.0)  # easting, northing (around Pruszków)


def square(x0: float, y0: float, side: float) -> dict[str, object]:
    ring = [[x0, y0], [x0 + side, y0], [x0 + side, y0 + side], [x0, y0 + side], [x0, y0]]
    return {"type": "Polygon", "coordinates": [ring]}


def feature(object_id: int, geometry: dict[str, object], name: str = "Nowa Wieś ") -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "ID_KONTURU": object_id,
            "STATUS": "aktualny",
            "NAZWA_TG": name,
            "NAZWA_OG": name,
            "KOPALINA": "KAMIENIE ŁAMANE I BLOCZNE",
        },
    }


def serve(
    layers: dict[int, tuple[int, list[list[dict[str, object]]]]], seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    """Answers `query` like the MapServer: `layers` maps layer id -> (count, pages of features)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        layer_id = int(request.url.path.split("/")[-2])
        count, pages = layers[layer_id]
        if request.url.params.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": count})
        # Like the server: a window of the features, from resultOffset, at most resultRecordCount long.
        flat = [item for page in pages for item in page]
        start = int(request.url.params["resultOffset"])
        window = flat[start : start + int(request.url.params["resultRecordCount"])]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": window})

    return handler


def test_live_sample_has_the_fields_the_parser_reads() -> None:
    # The real answer for layer 2 (tereny górnicze) in a 60 km box, two of its 15 features, saved 21.10.2026.
    frame = _read_layer("mining_terrain", (LIVE_DIR / "midas_tereny_gorny_2features.geojson").read_bytes())
    assert len(frame) == 2
    assert frame["object_id"].iloc[0] == 897
    assert frame["status"].iloc[0] == "aktualny"
    assert frame["name"].iloc[0] == "Nowa Wieś"  # the service pads the name with a space
    assert frame.crs.to_epsg() == 2180
    assert set(frame["kind"]) == {"mining_terrain"}


def test_live_count_is_a_plain_json_count() -> None:
    assert json.loads((LIVE_DIR / "midas_tereny_gorny_count.json").read_text()) == {"count": 15}


def test_fetch_pages_both_layers_in_epsg_2180(tmp_path: Path, make_client: ClientFactory) -> None:
    layers = {
        1: (
            3,
            [
                [feature(1, square(621_000, 471_000, 500)), feature(2, square(625_000, 475_000, 500))],
                [feature(3, square(628_000, 478_000, 500))],
            ],
        ),
        2: (0, []),
    }
    seen: list[httpx.Request] = []
    connector = MidasConnector(SPEC, bbox=BBOX, page_size=2)
    connector.fetch(make_client(serve(layers, seen)), SnapshotStore(tmp_path))
    queries = [r for r in seen if r.url.path.endswith("/query")]
    assert {r.url.path.split("/")[-2] for r in queries} == {"1", "2"}
    first = queries[0].url.params
    assert first["inSR"] == "2180" and first["outSR"] == "2180"
    assert first["geometry"] == "620000.0,470000.0,630000.0,480000.0"
    assert first["geometryType"] == "esriGeometryEnvelope"
    layer_one = [r for r in queries if r.url.path.endswith("/1/query") and "resultOffset" in r.url.params]
    assert [int(r.url.params["resultOffset"]) for r in layer_one] == [
        0,
        2,
    ]  # one page of 2, then the last one


def test_parse_keeps_name_and_mineral_and_clips_to_the_bbox(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    whole = feature(1, square(610_000, 465_000, 30_000))  # a whole mining area, larger than the bbox
    inside = feature(2, square(621_000, 471_000, 500), name="Piaski ")
    layers = {1: (0, []), 2: (2, [[whole, inside]])}
    store = SnapshotStore(tmp_path)
    connector = MidasConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(serve(layers, [])), store)
    table = connector.parse(snapshot)
    assert list(table["kind"]) == ["mining_terrain", "mining_terrain"]
    assert sorted(table["name"]) == ["Nowa Wieś", "Piaski"]
    assert table.crs.to_epsg() == 2180
    assert table.total_bounds[0] == pytest.approx(BBOX[0]) and table.total_bounds[1] == pytest.approx(BBOX[1])
    clipped = table[table["object_id"] == 1].geometry.iloc[0]
    assert clipped.area == pytest.approx(10_000 * 10_000)
    assert (table[table["object_id"] == 2]["mineral"] == "KAMIENIE ŁAMANE I BLOCZNE").all()


def test_both_layers_are_stored_with_their_kind(tmp_path: Path, make_client: ClientFactory) -> None:
    layers = {
        1: (1, [[feature(7, square(621_000, 471_000, 500))]]),
        2: (1, [[feature(8, square(622_000, 472_000, 500))]]),
    }
    connector = MidasConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(serve(layers, [])), SnapshotStore(tmp_path))
    table = connector.parse(snapshot)
    assert sorted(table["kind"]) == sorted(LAYERS)
    assert set(table.loc[table["kind"] == "mining_area", "object_id"]) == {7}


def test_no_features_is_a_real_empty_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    layers = {1: (0, []), 2: (0, [])}
    connector = MidasConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(serve(layers, [])), SnapshotStore(tmp_path))
    table = connector.parse(snapshot)
    assert table.empty and table.crs.to_epsg() == 2180


def test_short_answer_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    # The count says 5 features but only one comes back: never store a truncated layer.
    layers = {1: (5, [[feature(1, square(621_000, 471_000, 500))]]), 2: (0, [])}
    with pytest.raises(ValueError, match="got 1 of 5"):
        MidasConnector(SPEC, bbox=BBOX).fetch(make_client(serve(layers, [])), SnapshotStore(tmp_path))


def test_arcgis_error_on_http_200_is_raised(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"error": {"code": 499, "message": "Token Required"}})

    with pytest.raises(ValueError, match="Token Required"):
        MidasConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_missing_count_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"features": []})

    with pytest.raises(ValueError, match="no count"):
        MidasConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_degrees_instead_of_metres_are_rejected(tmp_path: Path) -> None:
    # A service answer in WGS84 would put every area near (0, 0) in metres: fail loudly.
    degrees = feature(
        1, {"type": "Polygon", "coordinates": [[[21.0, 52.2], [21.1, 52.2], [21.1, 52.3], [21.0, 52.2]]]}
    )
    snapshot_path = tmp_path / "midas.zip"
    with zipfile.ZipFile(snapshot_path, "w") as archive:
        archive.writestr("request.json", json.dumps({"layers": {"mining_area": 1}, "bbox": list(BBOX)}))
        archive.writestr(
            "mining_area.geojson", json.dumps({"type": "FeatureCollection", "features": [degrees]})
        )
    snapshot = Snapshot(
        source_id="midas",
        fetched_at=datetime(2026, 10, 21, tzinfo=UTC),
        sha256="0" * 64,
        path=snapshot_path,
        size_bytes=snapshot_path.stat().st_size,
    )
    with pytest.raises(ValueError, match="outside Poland"):
        MidasConnector(SPEC, bbox=BBOX).parse(snapshot)


def test_identical_answers_store_one_object(tmp_path: Path, make_client: ClientFactory) -> None:
    store = SnapshotStore(tmp_path)
    layers = {1: (1, [[feature(1, square(621_000, 471_000, 500))]]), 2: (0, [])}
    first = MidasConnector(SPEC, bbox=BBOX).fetch(make_client(serve(layers, [])), store)
    second = MidasConnector(SPEC, bbox=BBOX).fetch(make_client(serve(layers, [])), store)
    assert first.sha256 == second.sha256
    assert second.new_content is False
