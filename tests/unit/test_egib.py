"""EGiB WFS connector against recorded (synthetic) pages. No network: httpx MockTransport serves the fixtures."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import load_sources
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.egib import EgibConnector, tile_bbox
from tests.conftest import FIXTURES

FIX = FIXTURES / "egib"
OGC_FIXTURE = FIXTURES / "ogc" / "ows_exception.xml"
TILE_A = 500_000.0  # x of tile A's left edge; tile B starts at 501 000


def spec():  # type: ignore[no-untyped-def]
    return next(s for s in load_sources().sources if s.id == "egib")


def serve(pages: dict[tuple[float, int], bytes]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer GetFeature requests from `pages`, keyed by (tile xmin, startIndex)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "bbox" not in request.url.params:  # robots.txt and anything else: no rules, not data
            return httpx.Response(404)
        requests.append(request)
        xmin = float(request.url.params["bbox"].split(",")[0])
        start = int(request.url.params["startIndex"])
        body = pages.get((xmin, start))
        if body is None:
            return httpx.Response(
                200, content=(FIX / "empty.gml").read_bytes(), headers={"Content-Type": "text/xml"}
            )
        return httpx.Response(200, content=body, headers={"Content-Type": "text/xml"})

    handler.requests = requests  # type: ignore[attr-defined]
    return handler


def read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_tiles_are_aligned_to_the_grid_and_cover_the_bbox() -> None:
    assert tile_bbox((500_100, 500_100, 501_500, 500_200)) == [
        (500_000, 500_000, 501_000, 501_000),
        (501_000, 500_000, 502_000, 501_000),
    ]
    # Two inputs that touch the same tile share it.
    assert len(tile_bbox((500_100, 500_100, 500_200, 500_200))) == 1


def test_pages_are_followed_until_all_features_are_read(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    handler = serve({(TILE_A, 0): read("tile_a_page1.gml"), (TILE_A, 2): read("tile_a_page2.gml")})
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)], page_size=2)
    with make_client(handler) as client:
        snapshot = connector.fetch(client, SnapshotStore(tmp_path))

    starts = [int(r.url.params["startIndex"]) for r in handler.requests]  # type: ignore[attr-defined]
    assert starts == [0, 2]
    table = connector.parse(snapshot)
    assert sorted(table["parcel_id"]) == ["146510_8.0502.55", "146510_8.0502.56", "146510_8.0502.57"]
    assert table.crs.to_epsg() == 2180


def test_parcels_on_a_tile_edge_are_kept_once(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    handler = serve(
        {
            (TILE_A, 0): read("tile_a_page1.gml"),
            (TILE_A, 2): read("tile_a_page2.gml"),
            (TILE_A + 1_000, 0): read("tile_b_page1.gml"),
        }
    )
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 501_500, 500_200)], page_size=2)
    with make_client(handler) as client:
        table = connector.parse(connector.fetch(client, SnapshotStore(tmp_path)))
    assert table["parcel_id"].is_unique
    assert len(table) == 3


def test_empty_tile_gives_an_empty_table(tmp_path: Path, make_client: Callable[..., PoliteClient]) -> None:
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)])
    with make_client(serve({})) as client:
        table = connector.parse(connector.fetch(client, SnapshotStore(tmp_path)))
    assert len(table) == 0
    assert table.crs.to_epsg() == 2180


def test_wfs_exception_is_an_error_not_data(tmp_path: Path, make_client: Callable[..., PoliteClient]) -> None:
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)])
    with (
        make_client(lambda _: httpx.Response(200, content=OGC_FIXTURE.read_bytes())) as client,
        pytest.raises(RuntimeError, match="Unsupported service"),
    ):
        connector.fetch(client, SnapshotStore(tmp_path))


def test_no_candidate_area_refuses_to_download(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    connector = EgibConnector(spec(), bboxes=[])
    with make_client(serve({})) as client, pytest.raises(ValueError, match="brak obszarów"):
        connector.fetch(client, SnapshotStore(tmp_path))


def test_the_bundle_is_the_snapshot_and_pages_are_objects_not_manifest_lines(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    store = SnapshotStore(tmp_path)
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)], page_size=2)
    handler = serve({(TILE_A, 0): read("tile_a_page1.gml"), (TILE_A, 2): read("tile_a_page2.gml")})
    with make_client(handler) as client:
        snapshot = connector.fetch(client, store)
    assert snapshot.path.suffix == ".json"
    assert len(store.history("egib")) == 1  # one fetch, one manifest line
    assert len(list((tmp_path / "egib" / "objects").rglob("*.gml.gz"))) == 2  # pages are kept gzipped


def test_a_refetch_that_differs_only_by_the_response_timestamp_stores_no_new_pages(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    store = SnapshotStore(tmp_path)
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)], page_size=2)
    page1, page2 = read("tile_a_page1.gml"), read("tile_a_page2.gml")
    stamped = page1.replace(
        b"<wfs:FeatureCollection", b'<wfs:FeatureCollection timeStamp="2030-01-01T00:00:00"', 1
    )
    assert stamped != page1  # the test really changes the bytes the server sends
    with make_client(serve({(TILE_A, 0): page1, (TILE_A, 2): page2})) as client:
        connector.fetch(client, store)
    objects = list((tmp_path / "egib" / "objects").rglob("*.gml.gz"))
    assert len(objects) == 2
    with make_client(serve({(TILE_A, 0): stamped, (TILE_A, 2): page2})) as client:
        connector.fetch(client, store)
    assert list((tmp_path / "egib" / "objects").rglob("*.gml.gz")) == objects
    assert len(store.history("egib")) == 2  # both fetches still recorded


def test_pages_stored_plain_by_older_code_still_parse_and_compact_to_the_same_parcels(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    store = SnapshotStore(tmp_path)
    connector = EgibConnector(spec(), bboxes=[(500_100, 500_100, 500_900, 500_900)], page_size=2)
    handler = serve({(TILE_A, 0): read("tile_a_page1.gml"), (TILE_A, 2): read("tile_a_page2.gml")})
    with make_client(handler) as client:
        snapshot = connector.fetch(client, store)
    fresh = connector.parse(snapshot)
    objects = tmp_path / "egib" / "objects"
    for packed in objects.rglob("*.gml.gz"):  # what a store written before compression looks like
        packed.with_suffix("").write_bytes(gzip.decompress(packed.read_bytes()))
        packed.unlink()
    assert sorted(connector.parse(snapshot).parcel_id) == sorted(fresh.parcel_id)
    files, before, after = store.compact_objects("egib", ".gml")
    assert files == 2 and 0 < after < before
    assert not list(objects.rglob("*.gml")) and len(list(objects.rglob("*.gml.gz"))) == 2
    assert sorted(connector.parse(snapshot).parcel_id) == sorted(fresh.parcel_id)
