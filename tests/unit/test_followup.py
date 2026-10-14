"""`dclh discover-followup`: every section on canned answers. The real services need a machine in Poland."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pyproj import Transformer
from typer.testing import CliRunner

from dclh_pipeline import cli, followup
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import Settings
from tests.conftest import FIXTURES

ClientFactory = Callable[..., PoliteClient]

WFS_FEATURES = """<?xml version="1.0"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:ms="http://mapserver.gis.umn.edu/mapserver"
    xmlns:gml="http://www.opengis.net/gml/3.2">
  <wfs:member><ms:BDOT10k_powiaty>
    <ms:TERYT>1421</ms:TERYT><ms:nazwa>pruszkowski</ms:nazwa>
    <ms:url>https://opendata.geoportal.gov.pl/bdot10k/SHP/14/1421_SHP.zip</ms:url>
    <ms:msGeometry><gml:Polygon><gml:posList>1 2 3 4</gml:posList></gml:Polygon></ms:msGeometry>
  </ms:BDOT10k_powiaty></wfs:member>
  <wfs:member><ms:BDOT10k_powiaty><ms:TERYT>1465</ms:TERYT><ms:nazwa>warszawski</ms:nazwa></ms:BDOT10k_powiaty></wfs:member>
</wfs:FeatureCollection>"""


def _router(
    routes: dict[str, Callable[[httpx.Request], httpx.Response]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        for needle, answer in routes.items():
            if needle in str(request.url):
                return answer(request)
        return httpx.Response(404, text="not routed in this test")

    return handler


def _ok(body: str | bytes, content_type: str = "text/xml") -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(200, content=body, headers={"content-type": content_type})


# --- 1. bdot10k ---------------------------------------------------------------------------------


def test_flatten_features_reads_leaf_fields_and_finds_the_download_link() -> None:
    features = followup.flatten_features(WFS_FEATURES.encode())
    assert [f["TERYT"] for f in features] == ["1421", "1465"]
    assert followup.link_fields(features) == {
        "url": "https://opendata.geoportal.gov.pl/bdot10k/SHP/14/1421_SHP.zip"
    }


def test_bdot10k_verdict_names_the_link_field(make_client: ClientFactory) -> None:
    lines = followup.bdot10k(make_client(_router({"BDOT": _ok(WFS_FEATURES)})))
    assert "Verdict: download link in field 'url'" in lines[-1]
    assert any("fields:" in line and "TERYT" in line for line in lines)


def test_bdot10k_without_a_link_says_so(make_client: ClientFactory) -> None:
    body = WFS_FEATURES.replace(
        "<ms:url>https://opendata.geoportal.gov.pl/bdot10k/SHP/14/1421_SHP.zip</ms:url>", ""
    )
    assert "no download link" in followup.bdot10k(make_client(_router({"BDOT": _ok(body)})))[-1]


# --- 2. kimpzp -----------------------------------------------------------------------------------


def test_featureinfo_uses_wms_1_1_1_lon_lat_order_and_falls_back_across_formats(
    make_client: ClientFactory,
) -> None:
    seen: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        if params["info_format"] == "application/json":
            return httpx.Response(200, text="<ServiceException>format not supported</ServiceException>")
        return httpx.Response(200, text="<FeatureInfo><przeznaczenie>MPZP: U/P</przeznaczenie></FeatureInfo>")

    lines = followup.kimpzp_featureinfo(
        make_client(_router({"Integracja": answer})), 20.86, 52.17, "wektor-pow"
    )
    assert [p["info_format"] for p in seen] == ["application/json", "text/xml"]
    first = seen[0]
    assert first["version"] == "1.1.1"
    assert first["srs"] == "EPSG:4326"
    minx, miny, maxx, maxy = (float(v) for v in first["bbox"].split(","))
    assert minx < 20.86 < maxx and miny < 52.17 < maxy  # lon first, then lat: the order WMS 1.1.1 fixes
    assert "przeznaczenie" in lines[1]


def test_reguly_point_comes_from_uldk_and_falls_back_to_the_village_centre(
    make_client: ClientFactory,
) -> None:
    to_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
    x, y = to_2180.transform(20.85, 52.16)
    polygon = f"POLYGON(({x} {y},{x + 100} {y},{x + 100} {y + 100},{x} {y + 100},{x} {y}))"
    uldk = _ok(f"0\nSRID=2180;{polygon}\n", "text/plain")
    (lon, lat), note = followup.reguly_point(make_client(_router({"uldk": uldk})))
    assert abs(lon - 20.85) < 0.01 and abs(lat - 52.16) < 0.01
    assert "ULDK" in note and "failed" not in note
    broken = _ok("-1 brak wyników", "text/plain")
    point, note = followup.reguly_point(make_client(_router({"uldk": broken})))
    assert point == followup.REGULY_CENTRE and note.startswith("village centre")


def test_kimpzp_verdict_reports_wfs_and_featureinfo(make_client: ClientFactory) -> None:
    capabilities = (FIXTURES / "ogc" / "wfs_capabilities.xml").read_bytes()

    def wms_or_wfs(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("request") == "GetCapabilities":
            return httpx.Response(200, content=capabilities, headers={"content-type": "text/xml"})
        return httpx.Response(200, text="zone info")

    routes = {"uldk": _ok("-1 x", "text/plain"), "Integracja": wms_or_wfs}
    verdict = followup.kimpzp(make_client(_router(routes)))[-1]
    assert verdict == "Verdict: WFS available; GetFeatureInfo answers at Reguły"


# --- 3. isok -------------------------------------------------------------------------------------


def test_service_links_keeps_services_and_downloads_only() -> None:
    html = (
        '<a href="/wfs/hazard?x=1">WFS</a> <a href="https://x.pl/atom/feed.xml">ATOM</a> '
        '<a href="/o-nas.html">About</a> <a href="/data/zone.zip">ZIP</a>'
    )
    links = followup.service_links(html, "https://wody.isok.gov.pl/inspire.html")
    assert "https://wody.isok.gov.pl/wfs/hazard?x=1" in links
    assert "https://wody.isok.gov.pl/data/zone.zip" in links
    assert all("o-nas" not in link for link in links)


def test_isok_lists_links_and_tests_the_wfs(make_client: ClientFactory) -> None:
    capabilities = (FIXTURES / "ogc" / "wfs_capabilities.xml").read_bytes()
    page = '<a href="https://wody.isok.gov.pl/wfs/hazard?service=WFS">WFS</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("request") == "GetCapabilities":
            return httpx.Response(200, content=capabilities, headers={"content-type": "text/xml"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    lines = followup.isok(make_client(_router({"wody.isok.gov.pl": handler})))
    assert any("GetCapabilities ok" in line for line in lines)
    assert lines[-1] == "Verdict: 1 links, 1 OGC services answered GetCapabilities"


# --- 4. ru ---------------------------------------------------------------------------------------


def test_api_hints_keep_gov_pl_addresses_and_api_paths_only() -> None:
    source = 'fetch("https://api.rejestr.gov.pl/v1/plans"); x="https://angular.io/docs"; u=`/gateway/api/search`; q="/home"'
    urls, paths = followup.api_hints(source)
    assert urls == ["https://api.rejestr.gov.pl/v1/plans"]
    assert paths == ["/gateway/api/search"]


def test_js_files_are_found_anywhere_in_the_manifest() -> None:
    manifest = {
        "exposes": [{"file": "chunk-A.js"}],
        "shared": {"x": {"entry": "./vendor.mjs"}},
        "name": "published-fe",
    }
    assert followup.js_files(manifest, "https://h.pl/published-fe/remoteEntry.json") == [
        "https://h.pl/published-fe/chunk-A.js",
        "https://h.pl/published-fe/vendor.mjs",
    ]


def test_ru_scans_the_listed_files(make_client: ClientFactory) -> None:
    manifest = json.dumps({"exposes": [{"file": "chunk-A.js"}]})
    js = 'const a = "https://mapy.rejestr.gov.pl/ows/wfs"; const b = "/backend/api/zones";'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("remoteEntry.json"):
            return (
                httpx.Response(200, text=manifest)
                if "published-fe" in request.url.path
                else httpx.Response(404)
            )
        return httpx.Response(200, text=js)

    lines = followup.ru(make_client(_router({"rejestr-urbanistyczny": handler})))
    assert "  address: https://mapy.rejestr.gov.pl/ows/wfs" in lines
    assert "  path: /backend/api/zones" in lines
    assert lines[-1] == "Verdict: 1 gov.pl addresses and 1 api-like paths in 1 files"


def test_ru_skips_shared_libraries_and_survives_a_file_that_loops(make_client: ClientFactory) -> None:
    """Shared Angular libraries say nothing about the API, and one manifest entry redirected in a loop on the VPS."""
    manifest = json.dumps(
        {
            "name": "published-fe",
            "shared": [
                {"outFileName": "_angular_core.SwIrv27wUc.js"},
                {"outFileName": "@angular/core/event-dispatch-contract.min.js"},
            ],
            "exposes": [{"outFileName": "chunk-A.js"}, {"outFileName": "chunk-B.js"}],
        }
    )
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        fetched.append(path)
        if path.endswith("remoteEntry.json"):
            return httpx.Response(200, text=manifest) if "published-fe" in path else httpx.Response(404)
        if "chunk-B" in path:
            raise httpx.TooManyRedirects("redirect loop")
        return httpx.Response(200, text='const a = "/backend/api/zones";')

    lines = followup.ru(make_client(_router({"rejestr-urbanistyczny": handler})))
    assert not any(
        "_angular_core" in p or "@angular" in p for p in fetched
    )  # shared libraries are never fetched
    assert "  path: /backend/api/zones" in lines  # chunk-A was scanned although chunk-B looped
    assert any("chunk-B.js" in line and "TooManyRedirects" in line for line in lines)
    assert any(
        line.startswith("published-fe: manifest keys") for line in lines
    )  # structure is always reported


def test_isok_does_not_test_viewer_pages_as_services(make_client: ClientFactory) -> None:
    capabilities = (FIXTURES / "ogc" / "wfs_capabilities.xml").read_bytes()
    page = (
        '<a href="/app/layer-viewer/index.html?service=WMS&request=GetCapabilities">viewer</a>'
        '<a href="https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_WORP_WMS">WMS</a>'
    )
    tested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("request") == "GetCapabilities":
            tested.append(request.url.path)
            return httpx.Response(200, content=capabilities, headers={"content-type": "text/xml"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    followup.isok(make_client(_router({"wody.isok.gov.pl": handler})))
    assert tested == ["/wss/INSPIRE/INSPIRE_NZ_HY_WORP_WMS"]


# --- the whole run -------------------------------------------------------------------------------


def test_a_dead_network_gives_four_failed_sections_not_an_exception(make_client: ClientFactory) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("no route")

    report = followup.run(make_client(down, retries=0))
    assert report.count("FAILED:") == 4  # every section reports its own failure
    assert report.count("Verdict: could not be checked") == 4
    for title, _ in followup.SECTIONS:
        assert title in report


def test_cli_saves_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path / "data", contact_email="tester@example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(followup, "run", lambda _client: "# REPORT")
    result = CliRunner().invoke(cli.app, ["discover-followup", "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    saved = list((tmp_path / "out").glob("followup-*.md"))
    assert len(saved) == 1 and saved[0].read_text() == "# REPORT"


def test_cli_needs_a_contact_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=tmp_path))
    result = CliRunner().invoke(cli.app, ["discover-followup", "--out", str(tmp_path)])
    assert result.exit_code != 0 and "DCLH_CONTACT_EMAIL" in result.output


def test_cli_prints_the_report_even_when_it_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the VPS the default folder was read-only for the container user and the report was lost."""
    settings = Settings(data_dir=tmp_path / "data", contact_email="tester@example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(followup, "run", lambda _client: "# REPORT")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder is needed")
    result = CliRunner().invoke(cli.app, ["discover-followup", "--out", str(blocker / "sub")])
    assert result.exit_code == 0, result.output
    assert "# REPORT" in result.output  # printed before any attempt to save
    assert "could not be saved" in result.output


def test_cli_default_folder_follows_the_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In the container the data dir is the writable /data volume; /app/data is not writable."""
    settings = Settings(data_dir=tmp_path / "data", contact_email="tester@example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(followup, "run", lambda _client: "# REPORT")
    result = CliRunner().invoke(cli.app, ["discover-followup"])
    assert result.exit_code == 0, result.output
    assert len(list((tmp_path / "data" / "discovery").glob("followup-*.md"))) == 1
