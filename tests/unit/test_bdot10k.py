from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.bdot10k import Bdot10kConnector

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="bdot10k", name="BDOT10k", used_for="t", plan="mvp", cadence="monthly")
ZIP_URL = "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/14/1421_GML.zip"
MEMBER = "PL.PZGiK.330.1421/BDOT10k/PL.PZGiK.330.1421__OT_BUBD_A.xml"


def building(i: int, x0: float, y0: float, function: str, status: str = "eksploatowany") -> str:
    """One OT_BUBD_A member as GUGiK writes it: srsName="EPSG:2180", easting first."""
    ring = f"{x0} {y0} {x0 + 10} {y0} {x0 + 10} {y0 + 10} {x0} {y0 + 10} {x0} {y0}"
    return f"""<gml:featureMember><ot:OT_BUBD_A gml:id="id_{i}">
<ot:kategoriaIstnienia>{status}</ot:kategoriaIstnienia>
<ot:funkcjaOgolnaBudynku>{function}</ot:funkcjaOgolnaBudynku>
<ot:funkcjaSzczegolowaBudynku>budynek jednorodzinny</ot:funkcjaSzczegolowaBudynku>
<ot:geometria><gml:Polygon gml:id="g{i}" srsName="EPSG:2180" srsDimension="2"><gml:exterior><gml:LinearRing>
<gml:posList>{ring}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></ot:geometria>
</ot:OT_BUBD_A></gml:featureMember>"""


def buildings_xml(members: list[str]) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:ot="urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0" gml:id="GUGiK_OT_BUBD_A">
{"".join(members)}
</gml:FeatureCollection>""".encode()


def powiat_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def index_answer(url: str) -> bytes:
    return f"""<wfs:FeatureCollection xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="1" numberReturned="1"><wfs:member><ms:BDOT10k_powiaty>
<ms:TERYT>1421</ms:TERYT><ms:URL_GML>{url}</ms:URL_GML></ms:BDOT10k_powiaty></wfs:member></wfs:FeatureCollection>
""".encode()


def serve(
    index: bytes, archive: bytes, seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        if request.url.host == "mapy.geoportal.gov.pl":
            return httpx.Response(200, content=index)
        return httpx.Response(200, content=archive)

    return handler


SAMPLE = {
    MEMBER: buildings_xml(
        [
            building(1, 625_000, 480_000, "budynki mieszkalne"),
            building(2, 625_100, 480_000, "budynki mieszkalne", status="w budowie"),
            building(3, 625_200, 480_000, "budynki mieszkalne", status="zniszczony"),
            building(4, 625_300, 480_000, "budynki przemysłowe"),
        ]
    ),
    "PL.PZGiK.330.1421/BDOT10k/PL.PZGiK.330.1421__OT_SKDR_L.xml": b"<other/>",
}


def direct_url_down(
    index: bytes, archive: bytes, seen: list[httpx.Request]
) -> Callable[[httpx.Request], httpx.Response]:
    """Like `serve`, but the fixed download address answers 404: only the index can say where the zip is."""
    inner = serve(index, archive, seen)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ZIP_URL:
            seen.append(request)
            return httpx.Response(404)
        return inner(request)

    return handler


def test_fetch_asks_the_download_server_directly_without_the_index_and_parse_keeps_standing_residential_buildings(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    seen: list[httpx.Request] = []
    connector = Bdot10kConnector(SPEC, teryt="1421")
    snapshot = connector.fetch(
        make_client(serve(index_answer("https://wrong.example/x"), powiat_zip(SAMPLE), seen)),
        SnapshotStore(tmp_path),
    )
    assert [str(r.url) for r in seen] == [ZIP_URL]  # one request, no lookup in the index
    assert snapshot.url == ZIP_URL
    table = connector.parse(snapshot)
    # 1 standing + 1 under construction; the ruin and the factory are left out.
    assert len(table) == 2
    assert set(table["status"]) == {"eksploatowany", "w budowie"}
    assert table.crs.to_epsg() == 2180
    assert tuple(table.total_bounds[:2]) == (625_000, 480_000)  # easting first, not swapped


def test_the_fixed_address_is_the_voivodeship_folder_and_the_four_digit_code() -> None:
    assert Bdot10kConnector(SPEC, teryt="0226").direct_url == (
        "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/02/0226_GML.zip"
    )
    assert Bdot10kConnector(SPEC, teryt="226").direct_url.endswith(
        "/02/0226_GML.zip"
    )  # padded like the TERYT


def test_when_the_fixed_address_fails_the_index_is_asked_and_its_url_is_used(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    moved = ZIP_URL.replace("schemat2021", "schemat2027")
    seen: list[httpx.Request] = []
    connector = Bdot10kConnector(SPEC, teryt="1421")
    snapshot = connector.fetch(
        make_client(direct_url_down(index_answer(moved), powiat_zip(SAMPLE), seen)), SnapshotStore(tmp_path)
    )
    assert snapshot.url == moved
    assert (
        "<Literal>1421</Literal>"
        in next(r for r in seen if r.url.host == "mapy.geoportal.gov.pl").url.params["FILTER"]
    )
    assert len(connector.parse(snapshot)) == 2


def test_a_timeout_on_the_fixed_address_also_falls_back_to_the_index(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    inner = serve(index_answer(ZIP_URL.replace("schemat2021", "schemat2027")), powiat_zip(SAMPLE), [])

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ZIP_URL:
            raise httpx.ReadTimeout("slow", request=request)
        return inner(request)

    snapshot = Bdot10kConnector(SPEC, teryt="1421").fetch(
        make_client(handler, retries=0), SnapshotStore(tmp_path)
    )
    assert "schemat2027" in snapshot.url


def test_download_link_to_another_host_is_refused(tmp_path: Path, make_client: ClientFactory) -> None:
    bad = "https://example.com/bdot10k/schemat2021/14/1421_GML.zip"
    with pytest.raises(ValueError, match="unexpected download URL"):
        Bdot10kConnector(SPEC, teryt="1421").fetch(
            make_client(direct_url_down(index_answer(bad), b"", [])), SnapshotStore(tmp_path)
        )


def test_link_to_another_powiat_is_refused(tmp_path: Path, make_client: ClientFactory) -> None:
    other = ZIP_URL.replace("1421_GML", "1418_GML")
    with pytest.raises(ValueError, match="unexpected download URL"):
        Bdot10kConnector(SPEC, teryt="1421").fetch(
            make_client(direct_url_down(index_answer(other), b"", [])), SnapshotStore(tmp_path)
        )


def test_unknown_powiat_is_reported(tmp_path: Path, make_client: ClientFactory) -> None:
    empty = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="0" numberReturned="0"/>'
    connector = Bdot10kConnector(SPEC, teryt="9999")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "mapy.geoportal.gov.pl":
            return httpx.Response(200, content=empty)
        return httpx.Response(404)  # no such file on the download server either

    with pytest.raises(ValueError, match="got 0"):
        connector.fetch(make_client(handler), SnapshotStore(tmp_path))


def test_a_non_zip_download_is_refused(tmp_path: Path, make_client: ClientFactory) -> None:
    with pytest.raises(ValueError, match="not a zip"):
        Bdot10kConnector(SPEC, teryt="1421").fetch(
            make_client(serve(index_answer(ZIP_URL), b"<html>maintenance</html>", [])),
            SnapshotStore(tmp_path),
        )


def test_zip_without_buildings_is_reported(tmp_path: Path) -> None:
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip({"x/readme.txt": b"x"}), suffix=".zip")
    with pytest.raises(ValueError, match="OT_BUBD_A"):
        Bdot10kConnector(SPEC, teryt="1421").parse(snapshot)


def test_coordinates_outside_poland_are_rejected(tmp_path: Path) -> None:
    far = {MEMBER: buildings_xml([building(1, 5_000, 5_000, "budynki mieszkalne")])}
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip(far), suffix=".zip")
    with pytest.raises(ValueError, match="outside Poland"):
        Bdot10kConnector(SPEC, teryt="1421").parse(snapshot)


def test_teryt_is_padded_and_checked() -> None:
    assert Bdot10kConnector(SPEC, teryt="201").teryt == "0201"
    with pytest.raises(ValueError, match="4 digits"):
        Bdot10kConnector(SPEC, teryt="14a1")


def forest(i: int, x0: float, y0: float, kind: str) -> str:
    """One OT_PTLZ_A feature (land cover: forest, tree rows, scrub) as GUGiK writes it."""
    ring = f"{x0} {y0} {x0 + 50} {y0} {x0 + 50} {y0 + 50} {x0} {y0 + 50} {x0} {y0}"
    return f"""<gml:featureMember><ot:OT_PTLZ_A gml:id="las_{i}">
<ot:rodzaj>{kind}</ot:rodzaj>
<ot:geometria><gml:Polygon gml:id="f{i}" srsName="EPSG:2180" srsDimension="2"><gml:exterior><gml:LinearRing>
<gml:posList>{ring}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></ot:geometria>
</ot:OT_PTLZ_A></gml:featureMember>"""


FOREST_MEMBER = "PL.PZGiK.330.1421/BDOT10k/PL.PZGiK.330.1421__OT_PTLZ_A.xml"


def test_parse_forest_keeps_only_forest_not_tree_rows_or_scrub(tmp_path: Path) -> None:
    members = [
        forest(1, 625_000, 480_000, "las"),
        forest(2, 625_100, 480_000, "zadrzewienie"),
        forest(3, 625_200, 480_000, "zagajnik"),
        forest(4, 625_300, 480_000, "las"),
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2" '
        'xmlns:ot="urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0">'
        + "".join(members)
        + "</gml:FeatureCollection>"
    ).encode()
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip({FOREST_MEMBER: xml}), suffix=".zip")
    table = Bdot10kConnector(SPEC, teryt="1421").parse_forest(snapshot)
    assert len(table) == 2
    assert set(table["kind"]) == {"las"}
    assert table.crs.to_epsg() == 2180
    assert tuple(table.total_bounds[:2]) == (625_000, 480_000)


def test_forest_member_missing_is_reported(tmp_path: Path) -> None:
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip(SAMPLE), suffix=".zip")
    with pytest.raises(ValueError, match="PTLZ"):
        Bdot10kConnector(SPEC, teryt="1421").parse_forest(snapshot)


ROADS_MEMBER = "PL.PZGiK.330.1421/BDOT10k/PL.PZGiK.330.1421__OT_SKDR_L.xml"


def road(i: int, x0: float, y0: float, road_class: str, status: str = "eksploatowany") -> str:
    """One OT_SKDR_L feature (a road centre line) as GUGiK writes it."""
    return f"""<gml:featureMember><ot:OT_SKDR_L gml:id="droga_{i}">
<ot:kategoriaIstnienia>{status}</ot:kategoriaIstnienia>
<ot:klasaDrogi>{road_class}</ot:klasaDrogi>
<ot:geometria><gml:LineString gml:id="r{i}" srsName="EPSG:2180" srsDimension="2">
<gml:posList>{x0} {y0} {x0 + 100} {y0}</gml:posList></gml:LineString></ot:geometria>
</ot:OT_SKDR_L></gml:featureMember>"""


def roads_snapshot(tmp_path: Path, members: list[str]):  # type: ignore[no-untyped-def]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2" '
        'xmlns:ot="urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0">'
        + "".join(members)
        + "</gml:FeatureCollection>"
    ).encode()
    return SnapshotStore(tmp_path).save("bdot10k", powiat_zip({ROADS_MEMBER: xml}), suffix=".zip")


def test_parse_roads_keeps_public_roads_and_drops_internal_roads_and_ruins(tmp_path: Path) -> None:
    snapshot = roads_snapshot(
        tmp_path,
        [
            road(1, 625_000, 480_000, "droga lokalna"),
            road(2, 625_000, 480_100, "droga wewnętrzna"),
            road(3, 625_000, 480_200, "droga dojazdowa"),
            road(4, 625_000, 480_300, "droga zbiorcza", status="zniszczony"),
            road(5, 625_000, 480_400, "droga ekspresowa"),
        ],
    )
    table = Bdot10kConnector(SPEC, teryt="1421").parse_roads(snapshot)
    assert sorted(table["kind"]) == ["droga dojazdowa", "droga ekspresowa", "droga lokalna"]
    assert table.crs.to_epsg() == 2180
    assert set(table.geometry.geom_type) == {"LineString"}
    assert tuple(table.total_bounds[:2]) == (625_000, 480_000)  # easting first, not swapped


def test_a_zip_without_the_road_member_gives_no_roads_not_an_error(tmp_path: Path) -> None:
    without_roads = {name: body for name, body in SAMPLE.items() if "SKDR" not in name}
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip(without_roads), suffix=".zip")
    assert Bdot10kConnector(SPEC, teryt="1421").parse_roads(snapshot).empty


def test_roads_outside_poland_are_rejected(tmp_path: Path) -> None:
    snapshot = roads_snapshot(tmp_path, [road(1, 10, 10, "droga lokalna")])
    with pytest.raises(ValueError, match=r"outside Poland|bounds"):
        Bdot10kConnector(SPEC, teryt="1421").parse_roads(snapshot)


def test_a_road_with_one_point_is_dropped_not_fatal(tmp_path: Path) -> None:
    """GUGiK's 0461 zip has a road line with a single point; GEOS cannot read it and it failed the search."""
    broken = road(2, 625_000, 480_100, "droga lokalna").replace(
        "625000 480100 625100 480100", "625000 480100"
    )
    snapshot = roads_snapshot(tmp_path, [road(1, 625_000, 480_000, "droga lokalna"), broken])
    table = Bdot10kConnector(SPEC, teryt="1421").parse_roads(snapshot)
    assert len(table) == 1
    assert tuple(table.total_bounds[:2]) == (625_000, 480_000)


def test_a_building_with_a_too_short_outline_is_dropped_not_fatal(tmp_path: Path) -> None:
    broken = building(2, 625_100, 480_000, "budynki mieszkalne").replace(
        "625100 480000 625110 480000 625110 480010 625100 480010 625100 480000", "625100 480000 625110 480000"
    )
    members = {MEMBER: buildings_xml([building(1, 625_000, 480_000, "budynki mieszkalne"), broken])}
    snapshot = SnapshotStore(tmp_path).save("bdot10k", powiat_zip(members), suffix=".zip")
    table = Bdot10kConnector(SPEC, teryt="1421").parse(snapshot)
    assert len(table) == 1
