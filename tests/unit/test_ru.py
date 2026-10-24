from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from shapely.geometry import box

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.zoning import plan_ogolny_kind
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.ru import RuMpzpConnector, RuPlanOgolnyConnector

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="ru", name="RU", used_for="t", plan="mvp", cadence="weekly")
LIVE = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19"
# The bbox of the live plan ogólny sample (easting, northing): the request asked 470000,620000,495000,645000
# northing-first.
BBOX = (620_000.0, 470_000.0, 645_000.0, 495_000.0)

SP_ZONE = """<wfs:member><app-pog:StrefaPlanistyczna gml:id="PL.ZIPPZP.9846_143206-POG_1POG-{i}SP_1">
<app-pog:idIIP><app-pog:Identyfikator><app-pog:przestrzenNazw>PL.ZIPPZP.9846/143206-POG</app-pog:przestrzenNazw>
<app-pog:lokalnyId>1POG-{i}SP</app-pog:lokalnyId><app-pog:wersjaId>1</app-pog:wersjaId></app-pog:Identyfikator>
</app-pog:idIIP><app-pog:oznaczenie>{i}SP</app-pog:oznaczenie><app-pog:symbol>SP</app-pog:symbol>
<app-pog:status xlink:href="http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce" xlink:title="obowiązujący"/>
<app-pog:geometria><gml:Polygon srsDimension="2" srsName="http://www.opengis.net/def/crs/EPSG/0/2180">
<gml:exterior><gml:LinearRing><gml:posList>{pos}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</app-pog:geometria>
<app-pog:nazwa xlink:href="https://www.gov.pl/static/zagospodarowanieprzestrzenne/codelist/RodzajStrefyPlanistycznejKod/strefaGospodarcza" xlink:title="strefa gospodarcza"/>
</app-pog:StrefaPlanistyczna></wfs:member>"""


def sp_zone(i: int, x0: float, y0: float, side: float) -> str:
    """One strefa gospodarcza written the way the service writes it: northing first."""
    ring = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
    return SP_ZONE.format(i=i, pos=" ".join(f"{y} {x}" for x, y in ring))


def sp_ring(i: int, ring: list[tuple[float, float]]) -> str:
    return SP_ZONE.format(i=i, pos=" ".join(f"{y} {x}" for x, y in ring))


def page(members: list[str], matched: int) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection
xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
xmlns:xlink="http://www.w3.org/1999/xlink"
xmlns:app-pog="https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
numberMatched="{matched}" numberReturned="{len(members)}" timeStamp="2026-10-19T09:55:35.996Z">
{"".join(members)}</wfs:FeatureCollection>""".encode()


def live(name: str, matched: int) -> bytes:
    """A live sample, with numberMatched cut to what the page holds so the fetch stops after it."""
    return re.sub(rb'numberMatched="\d+"', f'numberMatched="{matched}"'.encode(), (LIVE / name).read_bytes())


def serve(pages: list[bytes], seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        return httpx.Response(200, content=pages[int(request.url.params["startIndex"]) // 10])

    return handler


def test_live_plan_ogolny_sample_parses_with_zone_type_and_status(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    seen: list[httpx.Request] = []
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(
        make_client(serve([live("ru_pog_strefa_bbox_1421.gml", 10)], seen)), SnapshotStore(tmp_path)
    )
    assert seen[0].url.params["bbox"] == "470000.0,620000.0,495000.0,645000.0,urn:ogc:def:crs:EPSG::2180"
    assert seen[0].url.params["typeNames"] == "app-pog:StrefaPlanistyczna"
    table = connector.parse(snapshot)
    assert len(table) == 10
    assert table.crs.to_epsg() == 2180
    minx, miny, maxx, maxy = table.total_bounds
    # Easting, northing. Zones that cross the bbox edge reach a little past it.
    assert 615_000 < minx < maxx < 650_000 and 465_000 < miny < maxy < 500_000
    assert set(table["symbol"]) == {"SI", "SJ", "SK", "SN", "SU", "SW"}
    assert table["zone_href"].str.endswith("/strefaInfrastrukturalna").sum() == 2
    assert set(table["status_href"].str.rsplit("/", n=1).str[-1]) == {"adoption"}
    assert set(table["plan_id"]) == {"PL.ZIPPZP.11561/143403-POG"}
    # No strefa gospodarcza here, and drafts of other zones do not decide the zoning.
    assert all(
        plan_ogolny_kind(z, s) is None for z, s in zip(table["zone_href"], table["status_href"], strict=True)
    )


def test_live_mpzp_outline_has_a_status_and_no_designation(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    # The live outline lies near Szczawno-Zdrój, so ask with a bbox around it.
    bbox = (300_000.0, 320_000.0, 340_000.0, 360_000.0)
    connector = RuMpzpConnector(SPEC, bbox=bbox)
    snapshot = connector.fetch(
        make_client(serve([live("ru_mpzp_akt_1.gml", 1)], [])), SnapshotStore(tmp_path)
    )
    table = connector.parse(snapshot)
    assert len(table) == 1
    assert table["status_href"].iloc[0].endswith("/elaboration")
    assert table["plan_id"].iloc[0] == "PL.ZIPPZP.1/022103-MPZP"
    assert "symbol" not in table.columns
    assert table.geometry.iloc[0].area > 0


def test_pages_ten_at_a_time_until_number_matched(tmp_path: Path, make_client: ClientFactory) -> None:
    first = [sp_zone(i, 621_000 + 600 * i, 471_000, 500) for i in range(10)]
    pages = [
        page(first, matched=12),
        page([sp_zone(10, 630_000, 480_000, 500), sp_zone(11, 631_000, 480_000, 500)], 12),
    ]
    seen: list[httpx.Request] = []
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    table = connector.parse(connector.fetch(make_client(serve(pages, seen)), SnapshotStore(tmp_path)))
    assert [int(r.url.params["startIndex"]) for r in seen] == [0, 10]
    assert len(table) == 12
    assert set(table["status_href"].str.rsplit("/", n=1).str[-1]) == {"legalForce"}
    assert table["zone_href"].str.endswith("/strefaGospodarcza").all()


def test_short_answer_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    pages = [page([sp_zone(1, 621_000, 471_000, 500)], matched=5), page([], 5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=pages[min(int(request.url.params["startIndex"]), 1)])

    with pytest.raises(ValueError, match="got 1 of 5"):
        RuPlanOgolnyConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_easting_first_answer_is_rejected(tmp_path: Path, make_client: ClientFactory) -> None:
    members = [sp_zone(1, 471_000, 621_000, 500)]  # written with the axes the other way round
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(serve([page(members, 1)], [])), SnapshotStore(tmp_path))
    with pytest.raises(ValueError, match="axis order"):
        connector.parse(snapshot)


def test_a_zone_that_surrounds_a_small_box_without_touching_it_is_no_plan_not_an_error(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    # A parcel check asks for a few hundred metres. The server matches on envelopes, so a long L-shaped zone whose
    # extent covers the box is returned although the zone itself is nowhere near it.
    l_shape = [
        (600_000, 460_000),
        (660_000, 460_000),
        (660_000, 465_000),
        (605_000, 465_000),
        (605_000, 500_000),
        (600_000, 500_000),
        (600_000, 460_000),
    ]
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(
        make_client(serve([page([sp_ring(1, l_shape)], 1)], [])), SnapshotStore(tmp_path)
    )
    table = connector.parse(snapshot)
    assert len(table) == 1 and not table.geometry.intersects(box(*BBOX)).any()


def test_no_plans_is_a_real_empty_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    table = connector.parse(connector.fetch(make_client(serve([page([], 0)], [])), SnapshotStore(tmp_path)))
    assert table.empty and table.crs.to_epsg() == 2180
    assert "zone_href" in table.columns


def test_wfs_exception_is_raised_even_on_http_200(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"<ows:ExceptionReport>bad bbox</ows:ExceptionReport>")

    with pytest.raises(ValueError, match="exception"):
        RuMpzpConnector(SPEC, bbox=BBOX).fetch(make_client(handler), SnapshotStore(tmp_path))


def test_a_snapshot_of_one_layer_is_not_parsed_as_the_other(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    snapshot = RuPlanOgolnyConnector(SPEC, bbox=BBOX).fetch(
        make_client(serve([page([], 0)], [])), SnapshotStore(tmp_path)
    )
    with pytest.raises(ValueError, match="not app-mpzp"):
        RuMpzpConnector(SPEC, bbox=BBOX).parse(snapshot)


def refusing(pages: dict[tuple[int, int], bytes]) -> Callable[[httpx.Request], httpx.Response]:
    """Answers (startIndex, count) from `pages`; anything else gets the server's 400 "Invalid request"."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        key = (int(request.url.params["startIndex"]), int(request.url.params["count"]))
        if key in pages:
            return httpx.Response(200, content=pages[key])
        return httpx.Response(400, content=b"<ows:ExceptionReport>Invalid request</ows:ExceptionReport>")

    return handler


def test_a_broken_feature_is_skipped_and_the_rest_of_its_page_kept(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    # Live 19.10.2026: one MPZP outline in the 1421 bbox makes the server refuse every page that holds it.
    zones = [sp_zone(i, 621_000 + 600 * i, 471_000, 500) for i in range(13)]
    pages = {(0, 10): page(zones[:10], 13)}  # (10, 10) is refused
    for i in (10, 12):  # index 11 is the broken one
        pages[(i, 1)] = page([zones[i]], 13)
    for i in range(13, 20):
        pages[(i, 1)] = page([], 13)
    connector = RuPlanOgolnyConnector(SPEC, bbox=BBOX)
    snapshot = connector.fetch(make_client(refusing(pages)), SnapshotStore(tmp_path))
    table = connector.parse(snapshot)
    assert len(table) == 12
    assert "1POG-11SP" not in set(table["local_id"])
    with zipfile.ZipFile(snapshot.path) as archive:
        assert json.loads(archive.read("skipped.json")) == [11]


def test_a_server_that_refuses_everything_fails_instead_of_returning_an_empty_layer(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    with pytest.raises(ValueError, match="refused 10 features"):
        RuMpzpConnector(SPEC, bbox=BBOX).fetch(make_client(refusing({})), SnapshotStore(tmp_path))
