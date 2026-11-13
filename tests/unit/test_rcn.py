from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.rcn_import import land_rows
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.rcn import FORBIDDEN_COLUMNS, KEPT_FIELDS, RcnConnector, parse_page

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="rcn", name="RCN", used_for="t", plan="mvp", cadence="monthly")
BBOX = (200_000.0, 500_000.0, 210_000.0, 510_000.0)  # easting, northing (around Rzepin)
HEAD = (
    '<?xml version="1.0"?><wfs:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver" '
    'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:wfs="http://www.opengis.net/wfs/2.0" '
    'numberMatched="unknown" numberReturned="{n}">'
)


def feature(
    tx: str,
    parcel: str,
    *,
    x: float = 205_000.0,
    y: float = 505_000.0,
    powiat: str = "0805",
    tx_date: str = "2026-03-10",
    nier: str = "nieruchomoscGruntowaNiezabudowana",
    price: str = "100000",
    area: str = "1.0",
    parcel_area: str = "1.0",
    use: str = "gruntyRolne",
    share: str = "1/1",
    version: str = "2026-03-11T10:00:00Z",
) -> str:
    """One `ms:dzialki` member as the live service writes it: posList is northing first."""
    ring = [(x, y), (x + 100, y), (x + 100, y + 100), (x, y + 100), (x, y)]
    pos = " ".join(f"{py} {px}" for px, py in ring)
    return f"""<wfs:member><ms:dzialki gml:id="dzialki.{parcel}">
      <ms:GEOMETRIA><gml:Polygon srsName="urn:ogc:def:crs:EPSG::2180"><gml:exterior><gml:LinearRing>
        <gml:posList srsDimension="2">{pos}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></ms:GEOMETRIA>
      <ms:TERYT>{powiat}</ms:TERYT>
      <ms:TRAN_LOKALNY_ID_IIP>{tx}</ms:TRAN_LOKALNY_ID_IIP>
      <ms:TRAN_WERSJA_ID><gml:timePosition>{version}</gml:timePosition></ms:TRAN_WERSJA_ID>
      <ms:TRAN_OZNACZENIE_TRANS>2917/2025</ms:TRAN_OZNACZENIE_TRANS>
      <ms:TRAN_RODZAJ_TRANS>wolnyRynek</ms:TRAN_RODZAJ_TRANS>
      <ms:TRAN_SPRZEDAJACY>osobaFizyczna</ms:TRAN_SPRZEDAJACY>
      <ms:TRAN_KUPUJACY>osobaFizyczna</ms:TRAN_KUPUJACY>
      <ms:DOK_OZNACZENIE>Rep. A nr 2917/2025</ms:DOK_OZNACZENIE>
      <ms:DOK_DATA><gml:timePosition>{tx_date}</gml:timePosition></ms:DOK_DATA>
      <ms:DOK_TWORCA>NOTARIUSZ JAN PRZYKLADOWY W SLUBICACH</ms:DOK_TWORCA>
      <ms:NIER_RODZAJ>{nier}</ms:NIER_RODZAJ>
      <ms:NIER_UDZIAL>{share}</ms:NIER_UDZIAL>
      <ms:NIER_POW_GRUNTU>{area}</ms:NIER_POW_GRUNTU>
      <ms:NIER_CENA_BRUTTO>{price}</ms:NIER_CENA_BRUTTO>
      <ms:DZI_ID_DZIALKI>{parcel}</ms:DZI_ID_DZIALKI>
      <ms:DZI_PRZEZN_WMPZP>brakMPZPLubWZ</ms:DZI_PRZEZN_WMPZP>
      <ms:DZI_POW_EWID>{parcel_area}</ms:DZI_POW_EWID>
      <ms:DZI_SPOSOB_UZYT>{use}</ms:DZI_SPOSOB_UZYT>
      <ms:DZI_CENA_BRUTTO></ms:DZI_CENA_BRUTTO>
    </ms:dzialki></wfs:member>"""


def page(*members: str) -> bytes:
    return (HEAD.format(n=len(members)) + "".join(members) + "</wfs:FeatureCollection>").encode()


def serve(pages: list[bytes], seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        index = int(request.url.params["STARTINDEX"]) // int(request.url.params["COUNT"])
        return httpx.Response(200, content=pages[index] if index < len(pages) else page())

    return handler


def test_the_bbox_is_sent_northing_first(tmp_path: Path, make_client: ClientFactory) -> None:
    # Live check 12.11.2026: the service follows the EPSG axis order, so minx,miny,... returns another region.
    seen: list[httpx.Request] = []
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX, page_size=10)
    connector.fetch(make_client(serve([page(feature("T1", "P1"))], seen)), SnapshotStore(tmp_path))
    assert seen[0].url.params["BBOX"] == "500000.0,200000.0,510000.0,210000.0,urn:ogc:def:crs:EPSG::2180"
    assert seen[0].url.params["TYPENAMES"] == "ms:dzialki"
    assert seen[0].url.params["VERSION"] == "2.0.0"


def test_pages_until_a_short_page_and_keeps_only_the_requested_powiat(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    pages = [
        page(feature("T1", "P1"), feature("T2", "P2", powiat="0807")),
        page(feature("T3", "P3")),
    ]
    seen: list[httpx.Request] = []
    connector = RcnConnector(SPEC, powiat="805", bbox=BBOX, page_size=2)  # TERYT without the leading zero
    snapshot = connector.fetch(make_client(serve(pages, seen)), SnapshotStore(tmp_path))
    assert [int(r.url.params["STARTINDEX"]) for r in seen] == [0, 2]
    table = connector.parse(snapshot)
    assert sorted(table["tx_id"]) == ["T1", "T3"]


def test_parsed_frame_and_snapshot_hold_no_personal_data(tmp_path: Path, make_client: ClientFactory) -> None:
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX)
    snapshot = connector.fetch(
        make_client(serve([page(feature("T1", "P1")), page()], [])), SnapshotStore(tmp_path)
    )
    table = connector.parse(snapshot)
    assert FORBIDDEN_COLUMNS.isdisjoint(table.columns)
    assert not set(KEPT_FIELDS.values()) & FORBIDDEN_COLUMNS
    assert snapshot.path.name.endswith(".json.gz")  # kept gzipped (disk, 13.11.2026)
    raw = gzip.decompress(snapshot.path.read_bytes()).decode()
    for secret in ("NOTARIUSZ", "PRZYKLADOWY", "Rep. A", "2917/2025", "osobaFizyczna"):
        assert secret not in raw
    assert set(json.loads(raw)["records"][0]) <= {*KEPT_FIELDS, "powiat", "tx_date", "version", "x", "y"}


def test_points_are_easting_northing_in_epsg_2180(tmp_path: Path, make_client: ClientFactory) -> None:
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX)
    snapshot = connector.fetch(
        make_client(serve([page(feature("T1", "P1", x=205_000, y=505_000))], [])), SnapshotStore(tmp_path)
    )
    table = connector.parse(snapshot)
    assert table.crs.to_epsg() == 2180
    point = table.geometry.iloc[0]
    assert (round(point.x), round(point.y)) == (205_050, 505_050)


def test_a_transaction_on_several_parcels_is_one_row() -> None:
    members = [
        feature("T1", "P1", x=205_000, parcel_area="1.0", price="300000", area="3.0"),
        feature("T1", "P2", x=205_300, parcel_area="2.0", price="300000", area="3.0", use="inne"),
    ]
    _, records = parse_page(page(*members))
    from dclh_pipeline.sources.rcn import transactions_frame

    table = transactions_frame(records)
    assert len(table) == 1
    row = table.iloc[0]
    assert row["parcels"] == 2 and row["price_zl"] == 300_000 and row["area_ha"] == 3.0
    assert row["land_use"] == "mieszane"
    # Area-weighted: the 2 ha parcel pulls the point towards x = 205_350.
    assert table.geometry.iloc[0].x == pytest.approx((205_050 * 1 + 205_350 * 2) / 3)


def test_an_older_version_of_a_parcel_is_replaced() -> None:
    from dclh_pipeline.sources.rcn import transactions_frame

    _, records = parse_page(
        page(
            feature("T1", "P1", price="100", version="2026-01-01T00:00:00Z"),
            feature("T1", "P1", price="200", version="2026-02-01T00:00:00Z"),
        )
    )
    assert transactions_frame(records)["price_zl"].tolist() == [200]


def test_land_rows_drop_apartments() -> None:
    from dclh_pipeline.sources.rcn import transactions_frame

    _, records = parse_page(page(feature("T1", "P1"), feature("T2", "P2", nier="nieruchomoscLokalowa")))
    assert [r["tx_id"] for r in land_rows(transactions_frame(records))] == ["T1"]


def test_coordinates_outside_poland_are_rejected() -> None:
    from dclh_pipeline.sources.rcn import transactions_frame

    # A northing-first posList read the wrong way round lands far outside the country.
    _, records = parse_page(page(feature("T1", "P1", x=5_000_000, y=5_000_000)))
    with pytest.raises(ValueError, match="outside Poland"):
        transactions_frame(records)


def test_an_exception_report_is_an_error_not_an_empty_answer() -> None:
    xml = b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1"><ows:Exception/></ows:ExceptionReport>'
    with pytest.raises(ValueError, match="RCN returned an error"):
        parse_page(xml)


def test_control_characters_and_bad_utf8_in_a_text_field_do_not_break_the_page() -> None:
    # Live 12.11.2026: powiats 1206, 1219, 2209 and 2216 failed with "not well-formed (invalid token)".
    raw = page(feature("T1", "P1", share="1/1\x0b"), feature("T2", "P2")).replace(b"1/1\x0b", b"1/1\x0b\xff")
    returned, records = parse_page(raw)
    assert returned == 2
    assert sorted(r["tx"] for r in records) == ["T1", "T2"]


def serve_by_bbox(
    features: list[tuple[str, float, float]], seen: list[httpx.Request]
) -> Callable[..., httpx.Response]:
    """A server that answers each BBOX (northing first) with the features whose anchor lies inside it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        y0, x0, y1, x1 = (float(v) for v in request.url.params["BBOX"].split(",")[:4])
        inside = [f for f in features if x0 <= f[1] <= x1 and y0 <= f[2] <= y1]
        count, start = int(request.url.params["COUNT"]), int(request.url.params["STARTINDEX"])
        chunk = inside[start : start + count]
        return httpx.Response(200, content=page(*(feature(t, t, x=x, y=y) for t, x, y in chunk)))

    return handler


def test_a_box_over_the_page_limit_is_split_into_quadrants(
    tmp_path: Path, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Live 12.11.2026: Warsaw, Kraków, Gdańsk and Wrocław hold more than MAX_PAGES pages in their bbox.
    monkeypatch.setattr("dclh_pipeline.sources.rcn.MAX_PAGES", 2)
    corners = [
        (f"T{i}", 201_000.0 + i, 501_000.0 + i) for i in range(3)
    ]  # south-west quadrant: fits in 2 pages
    others = [("TNE", 209_000.0, 509_000.0), ("TEDGE", 205_000.0, 509_000.0)]  # TEDGE sits on the split line
    seen: list[httpx.Request] = []
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX, page_size=2)
    snapshot = connector.fetch(make_client(serve_by_bbox(corners + others, seen)), SnapshotStore(tmp_path))
    table = connector.parse(snapshot)
    assert sorted(table["tx_id"]) == sorted(t for t, _, _ in corners + others)  # every one, TEDGE once
    assert len({r.url.params["BBOX"] for r in seen}) == 5  # the whole box, then its four quadrants


def test_a_box_that_stays_over_the_limit_after_splitting_is_an_error(
    tmp_path: Path, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dclh_pipeline.sources.rcn.MAX_PAGES", 1)
    monkeypatch.setattr("dclh_pipeline.sources.rcn.MAX_SPLIT_DEPTH", 1)
    pile = [(f"T{i}", 201_000.0, 501_000.0) for i in range(5)]  # one spot: no split can thin it
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX, page_size=2)
    with pytest.raises(ValueError, match="pages"):
        connector.fetch(make_client(serve_by_bbox(pile, [])), SnapshotStore(tmp_path))


def test_a_plain_snapshot_from_before_gzip_still_parses(tmp_path: Path, make_client: ClientFactory) -> None:
    connector = RcnConnector(SPEC, powiat="0805", bbox=BBOX)
    packed = connector.fetch(make_client(serve([page(feature("T1", "P1"))], [])), SnapshotStore(tmp_path))
    plain = SnapshotStore(tmp_path).save("rcn", gzip.decompress(packed.path.read_bytes()), suffix=".json")
    assert list(connector.parse(plain)["tx_id"]) == ["T1"]
