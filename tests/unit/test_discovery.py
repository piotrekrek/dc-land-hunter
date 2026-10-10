from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from defusedxml import EntitiesForbidden

from dclh_pipeline.config import OgcProbe, PageProbe, SourceSpec, load_sources
from dclh_pipeline.discovery import (
    BEGIN_MARK,
    END_MARK,
    ProbeOutcome,
    ProbeResult,
    SourceStatus,
    classify,
    discover,
    extract_endpoints,
    keyword_links,
    parse_capabilities,
    probe_ogc,
    probe_page,
    render_markdown,
    update_sources_doc,
)
from dclh_pipeline.http import PoliteClient
from tests.conftest import FIXTURES

ClientFactory = Callable[..., PoliteClient]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_wfs_capabilities() -> None:
    info = parse_capabilities(fixture("ogc/wfs_capabilities.xml"), "WFS")
    assert info["version"] == "2.0.0"
    assert info["title"] == "Usługa zbiorcza WFS EGiB"
    assert info["layers"] == ["ms:dzialki", "ms:obreby"]
    assert info["count_default"] == "1000"


def test_parse_wms_capabilities() -> None:
    info = parse_capabilities(fixture("ogc/wms_capabilities.xml"), "WMS")
    assert info["layer_count"] == 3
    assert "application/vnd.ogc.gml" in info["feature_info_formats"]


def test_exception_report_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported service"):
        parse_capabilities(fixture("ogc/ows_exception.xml"), "WFS")


def test_xml_entity_expansion_is_refused() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>'
    with pytest.raises(EntitiesForbidden):
        parse_capabilities(bomb, "WFS")


def test_extract_endpoints() -> None:
    html = fixture("pages/map_page.html").decode()
    found = extract_endpoints(html, "https://operator.example.pl/mapa")
    assert "https://operator.example.pl/dokumenty/wykaz-warunkow.xlsx" in found
    assert "https://gis.example.pl/arcgis/rest/services/Moce/MapServer" in found
    assert not any(u.endswith("/kontakt") for u in found)


def test_keyword_links() -> None:
    links = [("/dokumenty/wykaz.xlsx", "Wykaz wydanych warunków przyłączenia"), ("/kontakt", "Kontakt")]
    out = keyword_links(links, ["warunk"], "https://www.pse.pl/")
    assert out == [
        {"url": "https://www.pse.pl/dokumenty/wykaz.xlsx", "text": "Wykaz wydanych warunków przyłączenia"}
    ]


def test_probe_ogc(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.params["request"] == "GetCapabilities"
        assert request.url.params["version"] == "2.0.0"
        return httpx.Response(
            200, content=fixture("ogc/wfs_capabilities.xml"), headers={"content-type": "text/xml"}
        )

    probe = OgcProbe(kind="ogc", service="WFS", url="https://wfs.example.pl/egib")  # type: ignore[arg-type]
    result = probe_ogc(make_client(handler), probe)
    assert result.outcome is ProbeOutcome.OK
    assert result.details["layer_count"] == 2


def test_probe_ogc_exception_is_failure(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=fixture("ogc/ows_exception.xml"))

    probe = OgcProbe(kind="ogc", service="WFS", url="https://wfs.example.pl/x")  # type: ignore[arg-type]
    result = probe_ogc(make_client(handler), probe)
    assert result.outcome is ProbeOutcome.FAILED
    assert "Unsupported service" in (result.error or "")


def test_probe_page_scans_same_origin_scripts(make_client: ClientFactory) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/static/app.js":
            return httpx.Response(200, text='fetch("/api/gpz/moce.json").then(r => r.json())')
        return httpx.Response(
            200, content=fixture("pages/map_page.html"), headers={"content-type": "text/html"}
        )

    probe = PageProbe(kind="page", url="https://operator.example.pl/mapa", keywords=["warunk"])  # type: ignore[arg-type]
    result = probe_page(make_client(handler), probe)
    assert result.details["title"] == "Mapa dostępnych mocy"
    assert "https://operator.example.pl/api/gpz/moce.json" in result.details["endpoint_candidates"]
    assert len(result.details["keyword_links"]) == 1
    assert not any("cdn.example.org" in u for u in requested)  # third-party scripts are not fetched


def _r(kind: str, outcome: ProbeOutcome) -> ProbeResult:
    return ProbeResult(kind=kind, url="https://x.example.pl", outcome=outcome)


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([_r("ogc", ProbeOutcome.OK)], SourceStatus.VERIFIED),
        ([_r("ogc", ProbeOutcome.OK), _r("http", ProbeOutcome.FAILED)], SourceStatus.PARTIAL),
        ([_r("page", ProbeOutcome.OK)], SourceStatus.PARTIAL),  # endpoint still to be confirmed
        ([_r("page", ProbeOutcome.FAILED)], SourceStatus.FAILED),
        ([_r("browser", ProbeOutcome.SKIPPED)], SourceStatus.FAILED),
        ([], SourceStatus.NOT_CHECKED),
    ],
)
def test_classify(results: list[ProbeResult], expected: SourceStatus) -> None:
    spec = SourceSpec(id="x", name="X", used_for="t", plan="mvp", cadence="weekly")
    assert classify(spec, results) is expected


def test_discover_unreachable_hosts_degrade(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    specs = [s for s in load_sources().sources if s.id in {"gdos", "ekw"}]
    report = discover(specs, make_client(handler, retries=0), ran_from="test")
    by_id = {s.source_id: s for s in report.sources}
    assert by_id["gdos"].status is SourceStatus.FAILED
    assert "ConnectError" in (by_id["gdos"].probes[0].error or "")
    assert by_id["ekw"].status is SourceStatus.NOT_CHECKED  # manual: never probed
    assert "| ✗ | `gdos`" in render_markdown(report)


def test_update_sources_doc(tmp_path: Path) -> None:
    doc = tmp_path / "SOURCES.md"
    doc.write_text(f"# Sources\n\n{BEGIN_MARK}\nold\n{END_MARK}\n\nManual notes stay.\n", encoding="utf-8")
    update_sources_doc(doc, "| new |\n")
    text = doc.read_text(encoding="utf-8")
    assert "old" not in text and "| new |" in text and "Manual notes stay." in text
