from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from defusedxml import EntitiesForbidden

from dclh_pipeline.config import HttpProbe, OgcProbe, PageProbe, SourceSpec, load_sources
from dclh_pipeline.discovery import (
    BEGIN_MARK,
    END_MARK,
    DiscoveryReport,
    ProbeOutcome,
    ProbeResult,
    SourceDiscovery,
    SourceStatus,
    classify,
    discover,
    extract_endpoints,
    keyword_links,
    parse_capabilities,
    probe_http,
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


def test_redirect_is_recorded_and_listed_for_review(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "old.example.pl":
            return httpx.Response(301, headers={"location": "https://parked.example.net/sprzedaz"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    probe = HttpProbe(kind="http", method="GET", url="https://old.example.pl/mapa")  # type: ignore[arg-type]
    result = probe_http(make_client(handler), probe)
    assert result.redirect_status == 301
    assert result.final_url == "https://parked.example.net/sprzedaz"

    report = DiscoveryReport(
        started_at="2026-10-10T22:00:00",
        finished_at="2026-10-10T22:01:00",
        user_agent="test",
        ran_from="test",
        sources=[SourceDiscovery("x", "X", "mvp", SourceStatus.PARTIAL, [result])],
    )
    md = render_markdown(report)
    assert "Przekierowania do przeglądu" in md
    assert "inna domena" in md  # cross-domain redirects are the parked-domain risk


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


def test_incapsula_challenge_page_is_not_reported_as_ok(make_client: ClientFactory) -> None:
    challenge = b"<html><script src='/_Incapsula_Resource?SWJIYLWA=1'></script></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=challenge, headers={"content-type": "text/html"})

    probe = PageProbe(kind="page", url="https://www.uke.gov.pl/", keywords=[])  # type: ignore[arg-type]
    result = probe_page(make_client(handler), probe)
    assert result.outcome is ProbeOutcome.FAILED
    assert "challenge" in (result.error or "")
    assert result.details == {}  # no endpoint candidates taken from the challenge page


def test_parked_domain_redirect_is_flagged(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "www.enea-operator.pl":
            return httpx.Response(
                302, headers={"location": "https://www.aftermarket.pl/domena/enea-operator.pl/"}
            )
        return httpx.Response(
            200, text="<html>Domena na sprzedaż</html>", headers={"content-type": "text/html"}
        )

    probe = PageProbe(kind="page", url="https://www.enea-operator.pl/", keywords=[])  # type: ignore[arg-type]
    result = probe_page(make_client(handler), probe)
    assert result.outcome is ProbeOutcome.FAILED
    assert "aftermarket.pl" in (result.error or "")


def test_lookalike_host_is_not_a_parked_domain(make_client: ClientFactory) -> None:
    from dclh_pipeline.discovery import _challenge_error

    def ok(url: str) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>ok</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    assert _challenge_error(ok("https://mapa-dan.com/")) is None
    assert _challenge_error(ok("https://xsedo.com/")) is None
    assert _challenge_error(ok("https://dan.com/x")) is not None
    assert _challenge_error(ok("https://www.sedo.com:443/x")) is not None


def test_report_names_do_not_overwrite_same_day_runs(tmp_path: Path) -> None:
    from dclh_pipeline.discovery import DiscoveryReport, save_report

    first = DiscoveryReport("2026-10-10T22:30:00+00:00", "x", "ua", "vps", [])
    second = DiscoveryReport("2026-10-10T22:30:41+00:00", "x", "ua", "vps", [])
    a, _ = save_report(first, tmp_path)
    b, _ = save_report(second, tmp_path)
    assert a != b and a.exists() and b.exists()
    assert sorted(p.name for p in tmp_path.glob("discovery-*.json"))[-1] == b.name


def test_browser_actions_are_parsed_from_config() -> None:
    from dclh_pipeline.config import BrowserProbe

    probe = BrowserProbe.model_validate(
        {
            "kind": "browser",
            "url": "https://example.pl/",
            "actions": [{"action": "fill", "selector": "input[type=search]", "value": "Warszawa"}],
        }
    )
    assert probe.actions[0].action == "fill"
    assert probe.actions[0].value == "Warszawa"
