from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.isok import (
    PROBABILITY_02PCT,
    PROBABILITY_1PCT,
    IsokConnector,
    _read_page,
    zones_with_probability,
)

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="isok", name="ISOK", used_for="t", plan="mvp", cadence="monthly")
LIVE = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19/isok_nz_fluvial_1.gml"
BBOX = (620_000.0, 470_000.0, 630_000.0, 480_000.0)  # easting, northing (around Pruszków)


def zone(i: int, x0: float, y0: float, side: float, probability: str = "0.01") -> str:
    """One NZ.Fluvial member written the way the service writes it: northing first."""
    ring = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
    return ring_zone(i, ring, probability)


def ring_zone(i: int, ring: list[tuple[float, float]], probability: str = "0.01") -> str:
    pos = " ".join(f"{y} {x}" for x, y in ring)
    return f"""<wfs:member><wms_nz-core:NZ.Fluvial gml:id="NZ.Fluvial.{i}">
<wms_nz-core:validityperiod_end>2025-12-21T23:00:00Z</wms_nz-core:validityperiod_end>
<wms_nz-core:loo_qualitativelikelihood>scenariusz Q 1% (raz na 100 lat)</wms_nz-core:loo_qualitativelikelihood>
<wms_nz-core:loo_ql_probabilityofoccurrence>{probability}</wms_nz-core:loo_ql_probabilityofoccurrence>
<wms_nz-core:geom><gml:Polygon srsName="http://www.opengis.net/def/crs/EPSG/0/2180" srsDimension="2" gml:id="g{i}">
<gml:exterior><gml:LinearRing><gml:posList>{pos}</gml:posList></gml:LinearRing></gml:exterior>
</gml:Polygon></wms_nz-core:geom></wms_nz-core:NZ.Fluvial></wfs:member>"""


def page(members: list[str], matched: int) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection
xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
xmlns:wms_nz-core="http://inspire.ec.europa.eu/schemas/wms_nz-core/4.0"
numberMatched="{matched}" numberReturned="{len(members)}" timeStamp="2026-10-19T09:49:26.265Z">
{"".join(members)}</wfs:FeatureCollection>""".encode()


def serve(pages: list[bytes], seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        start = int(request.url.params["startIndex"])
        return httpx.Response(200, content=pages[start // 2])

    return handler


def test_live_sample_has_the_attributes_the_parser_reads() -> None:
    # The real response (one 10% zone, EPSG:4258 by default) carries the verified field names.
    frame = _read_page(LIVE.read_bytes())
    assert len(frame) == 1
    assert frame["loo_ql_probabilityofoccurrence"].iloc[0] == pytest.approx(0.1)
    assert "Q 10%" in frame["loo_qualitativelikelihood"].iloc[0]


def test_fetch_pages_and_parse_swaps_to_easting_first(tmp_path: Path, make_client: ClientFactory) -> None:
    pages = [
        page([zone(1, 621_000, 471_000, 500), zone(2, 625_000, 475_000, 500)], matched=3),
        page([zone(3, 628_000, 478_000, 500)], matched=3),
    ]
    seen: list[httpx.Request] = []
    connector = IsokConnector(SPEC, bbox=BBOX, page_size=2)
    snapshot = connector.fetch(make_client(serve(pages, seen)), SnapshotStore(tmp_path))
    assert [int(r.url.params["startIndex"]) for r in seen] == [0, 2]
    fes = seen[0].url.params["filter"]
    assert "<fes:Literal>0.01</fes:Literal>" in fes
    assert "<gml:lowerCorner>470000.0 620000.0</gml:lowerCorner>" in fes  # northing first
    table = connector.parse(snapshot)
    assert len(table) == 3
    assert table.crs.to_epsg() == 2180
    minx, miny, _, _ = table.total_bounds
    assert (minx, miny) == (621_000, 471_000)  # easting, northing
    assert table["scenario"].iloc[0].startswith("scenariusz Q 1%")


def test_parse_clips_zones_to_the_bbox_and_drops_other_probabilities(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    big = zone(1, 610_000, 465_000, 30_000)  # a whole river reach, larger than the bbox
    other = zone(2, 621_000, 471_000, 500, probability="0.1")
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([big, other], matched=2)], [])), SnapshotStore(tmp_path)
    )
    table = IsokConnector(SPEC, bbox=BBOX).parse(snapshot)
    assert len(table) == 1
    assert tuple(table.total_bounds) == BBOX
    assert table.geometry.iloc[0].area == pytest.approx(10_000 * 10_000)


def test_identical_answers_store_one_object(tmp_path: Path, make_client: ClientFactory) -> None:
    store = SnapshotStore(tmp_path)
    body = page([zone(1, 621_000, 471_000, 500)], matched=1)
    first = IsokConnector(SPEC, bbox=BBOX).fetch(make_client(serve([body], [])), store)
    second = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([body.replace(b"09:49:26", b"11:00:00")], [])), store
    )
    assert first.sha256 == second.sha256
    assert second.new_content is False


def test_no_zones_is_a_real_empty_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([], 0)], [])), SnapshotStore(tmp_path)
    )
    table = IsokConnector(SPEC, bbox=BBOX).parse(snapshot)
    assert table.empty and table.crs.to_epsg() == 2180


def test_easting_first_answer_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    # If the service ever wrote easting first, the swap would move every zone off the bbox: fail loudly.
    members = zone(1, 471_000, 621_000, 500)  # written with the axes the other way round
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([members], 1)], [])), SnapshotStore(tmp_path)
    )
    with pytest.raises(ValueError, match="axis order"):
        IsokConnector(SPEC, bbox=BBOX).parse(snapshot)


def test_a_reach_that_surrounds_a_small_box_without_touching_it_is_no_flood_not_an_error(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    # A parcel check asks for a few hundred metres. The server matches on envelopes, so a long L-shaped reach whose
    # extent covers the box is returned although the zone itself is nowhere near it: that is "no flood here".
    l_shape = [
        (600_000, 460_000),
        (640_000, 460_000),
        (640_000, 465_000),
        (605_000, 465_000),
        (605_000, 500_000),
        (600_000, 500_000),
        (600_000, 460_000),
    ]
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([ring_zone(1, l_shape)], 1)], [])), SnapshotStore(tmp_path)
    )
    table = IsokConnector(SPEC, bbox=BBOX).parse(snapshot)
    assert table.empty and table.crs.to_epsg() == 2180


def test_short_answer_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    # The server claims 5 zones but the next page is empty: never store a truncated layer.
    pages = [page([zone(1, 621_000, 471_000, 500)], matched=5), page([], 5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=pages[min(int(request.url.params["startIndex"]), 1)])

    with pytest.raises(ValueError, match="got 1 of 5"):
        IsokConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_wfs_exception_is_raised_even_on_http_200(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"<ows:ExceptionReport>bad filter</ows:ExceptionReport>")

    with pytest.raises(ValueError, match="exception"):
        IsokConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_snapshot_records_the_bbox(tmp_path: Path, make_client: ClientFactory) -> None:
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([], 0)], [])), SnapshotStore(tmp_path)
    )
    with zipfile.ZipFile(snapshot.path) as archive:
        assert b"620000.0" in archive.read("request.json")


def test_request_asks_for_the_1_and_0_2_percent_zones_in_one_filter(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    seen: list[httpx.Request] = []
    IsokConnector(SPEC, bbox=BBOX).fetch(make_client(serve([page([], 0)], seen)), SnapshotStore(tmp_path))
    fes = seen[0].url.params["filter"]
    assert "<fes:Or>" in fes
    assert "<fes:Literal>0.01</fes:Literal>" in fes
    assert "<fes:Literal>0.002</fes:Literal>" in fes
    assert "<fes:Literal>0.1</fes:Literal>" not in fes


def test_parse_keeps_the_0_2_and_1_percent_zones_apart(tmp_path: Path, make_client: ClientFactory) -> None:
    members = [
        zone(1, 621_000, 471_000, 500, probability="0.01"),
        zone(2, 623_000, 471_000, 500, probability="0.002"),
        zone(3, 625_000, 471_000, 500, probability="0.1"),  # 10%: not used
    ]
    snapshot = IsokConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page(members, 3)], [])), SnapshotStore(tmp_path)
    )
    table = IsokConnector(SPEC, bbox=BBOX).parse(snapshot)
    assert len(table) == 2
    one_pct = zones_with_probability(table, PROBABILITY_1PCT)
    small = zones_with_probability(table, PROBABILITY_02PCT)
    assert len(one_pct) == 1 and len(small) == 1
    assert one_pct.iloc[0].bounds[0] == pytest.approx(621_000)
    assert small.iloc[0].bounds[0] == pytest.approx(623_000)


def test_snapshot_from_before_the_0_2_percent_zones_is_refused(tmp_path: Path) -> None:
    # Such a snapshot has only 1% zones; reading it as "no 0.2% zone here" would be a false zero.
    store = SnapshotStore(tmp_path)
    old = io.BytesIO()
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr("request.json", json.dumps({"layer": "wms_nz-core:NZ.Fluvial", "bbox": list(BBOX)}))
    snapshot = store.save("isok", old.getvalue(), suffix=".zip", url=None, content_type="application/zip")
    with pytest.raises(ValueError, match=r"predates the 0\.2% zones"):
        IsokConnector(SPEC, bbox=BBOX).parse(snapshot)
