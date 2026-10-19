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


def test_fetch_follows_url_gml_and_parse_keeps_standing_residential_buildings(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    seen: list[httpx.Request] = []
    connector = Bdot10kConnector(SPEC, teryt="1421")
    snapshot = connector.fetch(
        make_client(serve(index_answer(ZIP_URL), powiat_zip(SAMPLE), seen)), SnapshotStore(tmp_path)
    )
    assert "<Literal>1421</Literal>" in seen[0].url.params["FILTER"]
    assert str(seen[1].url) == ZIP_URL
    assert snapshot.url == ZIP_URL
    table = connector.parse(snapshot)
    # 1 standing + 1 under construction; the ruin and the factory are left out.
    assert len(table) == 2
    assert set(table["status"]) == {"eksploatowany", "w budowie"}
    assert table.crs.to_epsg() == 2180
    assert tuple(table.total_bounds[:2]) == (625_000, 480_000)  # easting first, not swapped


def test_download_link_to_another_host_is_refused(tmp_path: Path, make_client: ClientFactory) -> None:
    bad = "https://example.com/bdot10k/schemat2021/14/1421_GML.zip"
    with pytest.raises(ValueError, match="unexpected download URL"):
        Bdot10kConnector(SPEC, teryt="1421").fetch(
            make_client(serve(index_answer(bad), b"", [])), SnapshotStore(tmp_path)
        )


def test_link_to_another_powiat_is_refused(tmp_path: Path, make_client: ClientFactory) -> None:
    other = ZIP_URL.replace("1421_GML", "1418_GML")
    with pytest.raises(ValueError, match="unexpected download URL"):
        Bdot10kConnector(SPEC, teryt="1421").fetch(
            make_client(serve(index_answer(other), b"", [])), SnapshotStore(tmp_path)
        )


def test_unknown_powiat_is_reported(tmp_path: Path, make_client: ClientFactory) -> None:
    empty = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="0" numberReturned="0"/>'
    with pytest.raises(ValueError, match="got 0"):
        Bdot10kConnector(SPEC, teryt="9999").fetch(
            make_client(serve(empty, b"", [])), SnapshotStore(tmp_path)
        )


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
