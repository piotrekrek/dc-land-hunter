"""Phase 0 source discovery spike (brief section 4, "Rules for every connector").

`dclh discover` checks every probe in `config/sources.yaml` and writes a JSON report plus a Markdown table
for `docs/SOURCES.md`. It must run from a machine in Poland: several services block foreign IPs.

Probe kinds:
- ogc:     GetCapabilities. Reports version, title, layer names and the WFS CountDefault (max features).
- http:    status, content type, size.
- page:    HTML page. Extracts candidate data endpoints from the page and its same-origin scripts.
- arcgis:  ArcGIS REST catalogue. Lists folders and services.
- browser: headless Chromium. Records XHR/fetch responses that look like data (only with --capture-network).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from defusedxml import ElementTree

from dclh_pipeline.config import (
    ArcgisProbe,
    BrowserProbe,
    HttpProbe,
    OgcProbe,
    PageProbe,
    Probe,
    SourceSpec,
)
from dclh_pipeline.http import PoliteClient, RobotsDisallowedError

OGC_VERSIONS = {"WFS": "2.0.0", "WMS": "1.3.0", "WMTS": "1.0.0", "CSW": "2.0.2"}
MAX_LIST = 60  # cap lists in the report so it stays readable
MAX_SCRIPTS = 8  # same-origin scripts scanned per page probe
SNIFF_BYTES = 256 * 1024

# Data-endpoint patterns searched for in HTML and JS.
_ENDPOINT_RE = re.compile(
    r"""(?P<url>(?:https?:)?//[^\s"'<>()\\]+|/[A-Za-z0-9_\-./]+[^\s"'<>()\\]*)""",
)
_DATA_HINTS = re.compile(
    r"(arcgis/rest/services|/MapServer|/FeatureServer|service=(?:wms|wfs|wmts|csw)|/wms\b|/wfs\b|/ows\b"
    r"|/geoserver/|/mapproxy/|\.geojson|\.json\b|/api/|\.xlsx?\b|\.csv\b|\.pdf\b|\.zip\b|\.gml\b|\.pbf\b)",
    re.IGNORECASE,
)


class ProbeOutcome(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class SourceStatus(StrEnum):
    VERIFIED = "verified"  # ✓ a data endpoint answered as expected
    PARTIAL = "partial"  # ⚠ reachable, but the data endpoint still needs confirming (or some probes failed)
    FAILED = "failed"  # ✗ nothing reachable
    NOT_CHECKED = "not_checked"  # manual / v2 / no probes


STATUS_ICON = {
    SourceStatus.VERIFIED: "✓",
    SourceStatus.PARTIAL: "⚠",
    SourceStatus.FAILED: "✗",
    SourceStatus.NOT_CHECKED: "–",
}

DATA_PROBE_KINDS = {"ogc", "http", "arcgis"}


@dataclass
class ProbeResult:
    kind: str
    url: str
    outcome: ProbeOutcome
    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    redirect_status: int | None = None  # status of the first hop, when the request was redirected
    elapsed_ms: int | None = None
    size_bytes: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SourceDiscovery:
    source_id: str
    name: str
    plan: str
    status: SourceStatus
    probes: list[ProbeResult]
    notes: str | None = None


@dataclass
class DiscoveryReport:
    started_at: str
    finished_at: str
    user_agent: str
    ran_from: str
    sources: list[SourceDiscovery]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- parsing helpers


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_capabilities(xml: bytes, service: str) -> dict[str, Any]:
    """Summarise an OGC GetCapabilities document. Raises ValueError on an exception report."""
    root = ElementTree.fromstring(xml)
    root_name = _local(root.tag)
    if "Exception" in root_name:
        texts = [el.text.strip() for el in root.iter() if el.text and el.text.strip()]
        raise ValueError(f"OGC exception: {' | '.join(texts)[:300]}")
    if "Capabilities" not in root_name:
        raise ValueError(f"unexpected root element <{root_name}>")

    info: dict[str, Any] = {"root": root_name, "version": root.get("version")}
    for el in root.iter():
        if _local(el.tag) == "Title" and el.text:
            info["title"] = el.text.strip()
            break

    names: list[str] = []
    if service == "WFS":
        for ft in root.iter():
            if _local(ft.tag) == "FeatureType":
                name = next((c.text for c in ft if _local(c.tag) == "Name" and c.text), None)
                if name:
                    names.append(name.strip())
        # WFS 2.0 advertises the server-side feature limit as the CountDefault constraint.
        for el in root.iter():
            if _local(el.tag) == "Constraint" and el.get("name") == "CountDefault":
                value = next((v.text for v in el.iter() if _local(v.tag) == "DefaultValue" and v.text), None)
                if value:
                    info["count_default"] = value.strip()
    elif service == "WMS":
        for layer in root.iter():
            if _local(layer.tag) == "Layer":
                name = next((c.text for c in layer if _local(c.tag) == "Name" and c.text), None)
                if name:
                    names.append(name.strip())
        info["feature_info_formats"] = sorted(
            {
                fmt.text.strip()
                for op in root.iter()
                if _local(op.tag) == "GetFeatureInfo"
                for fmt in op
                if _local(fmt.tag) == "Format" and fmt.text
            }
        )
    elif service == "WMTS":
        for layer in root.iter():
            if _local(layer.tag) == "Layer":
                ident = next((c.text for c in layer if _local(c.tag) == "Identifier" and c.text), None)
                if ident:
                    names.append(ident.strip())
    info["layer_count"] = len(names)
    info["layers"] = names[:MAX_LIST]
    return info


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.links: list[tuple[str, str]] = []  # (href, text)
        self.scripts: list[str] = []
        self._in_title = False
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "a" and attr.get("href"):
            self._href = attr["href"]
            self._text = []
        elif tag == "script" and attr.get("src"):
            self.scripts.append(attr["src"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data.strip()
        if self._href is not None:
            self._text.append(data)


def extract_endpoints(text: str, base_url: str) -> list[str]:
    """Find URLs in HTML/JS that look like data endpoints (JSON, ArcGIS, OGC, downloadable files)."""
    found: dict[str, None] = {}
    for match in _ENDPOINT_RE.finditer(text):
        raw = match.group("url").rstrip(".,;")
        if not _DATA_HINTS.search(raw):
            continue
        if raw.startswith("//"):
            raw = "https:" + raw
        url = urljoin(base_url, raw)
        if urlsplit(url).scheme in {"http", "https"}:
            found.setdefault(url, None)
    return list(found)[:MAX_LIST]


def keyword_links(links: list[tuple[str, str]], keywords: list[str], base_url: str) -> list[dict[str, str]]:
    if not keywords:
        return []
    kws = [k.lower() for k in keywords]
    out: dict[str, dict[str, str]] = {}
    for href, text in links:
        haystack = f"{href} {text}".lower()
        if any(k in haystack for k in kws):
            url = urljoin(base_url, href)
            out.setdefault(url, {"url": url, "text": text[:120]})
    return list(out.values())[:MAX_LIST]


# --------------------------------------------------------------------------- probes


# Bot-protection and domain-parking pages answer 200 but carry no data.
CHALLENGE_MARKERS = ("_Incapsula_Resource", "Incapsula incident", "Request unsuccessful", "cf-challenge")
PARKED_DOMAIN_HOSTS = ("aftermarket.pl", "sedo.com", "afternic.com", "dan.com")


def _challenge_error(resp: httpx.Response) -> str | None:
    """Explain why a 200 response is not real content, or return None."""
    body = resp.text[:20_000] if "text" in (resp.headers.get("content-type") or "") else ""
    if any(marker in body for marker in CHALLENGE_MARKERS):
        return "strona ochrony antybotowej (challenge), nie treść"
    host = urlsplit(str(resp.url)).netloc
    if any(host.endswith(p) for p in PARKED_DOMAIN_HOSTS):
        return f"przekierowanie na stronę sprzedaży domeny ({host})"
    return None


def _base_result(probe: Probe, resp: httpx.Response, started: float) -> ProbeResult:
    blocked = _challenge_error(resp) if resp.is_success else None
    return ProbeResult(
        kind=probe.kind,
        url=str(probe.url),
        outcome=ProbeOutcome.FAILED
        if blocked
        else (ProbeOutcome.OK if resp.is_success else ProbeOutcome.FAILED),
        error=blocked,
        status_code=resp.status_code,
        content_type=resp.headers.get("content-type"),
        final_url=str(resp.url),
        redirect_status=resp.history[0].status_code if resp.history else None,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        size_bytes=int(resp.headers["content-length"])
        if resp.headers.get("content-length", "").isdigit()
        else None,
    )


def probe_ogc(client: PoliteClient, probe: OgcProbe) -> ProbeResult:
    params = {"service": probe.service, "request": "GetCapabilities", "version": OGC_VERSIONS[probe.service]}
    started = time.monotonic()
    resp = client.get(str(probe.url), params=params)
    result = _base_result(probe, resp, started)
    result.size_bytes = len(resp.content)
    if resp.is_success:
        try:
            result.details = parse_capabilities(resp.content, probe.service)
        except Exception as exc:
            result.outcome = ProbeOutcome.FAILED
            result.error = f"{type(exc).__name__}: {exc}"[:400]
    return result


def probe_http(client: PoliteClient, probe: HttpProbe) -> ProbeResult:
    started = time.monotonic()
    if probe.method == "HEAD":
        resp = client.head(str(probe.url))
        result = _base_result(probe, resp, started)
        result.details = {
            k: resp.headers[k] for k in ("last-modified", "etag", "accept-ranges") if k in resp.headers
        }
        return result
    resp = client.get(str(probe.url))
    result = _base_result(probe, resp, started)
    result.size_bytes = len(resp.content)
    result.details = {"preview": resp.text[:300]} if "text" in (result.content_type or "") else {}
    if "json" in (result.content_type or ""):
        try:
            data = resp.json()
            result.details = {
                "json_keys": sorted(data)[:MAX_LIST] if isinstance(data, dict) else type(data).__name__
            }
        except ValueError as exc:
            result.error = f"invalid JSON: {exc}"
    return result


def probe_page(client: PoliteClient, probe: PageProbe) -> ProbeResult:
    started = time.monotonic()
    resp = client.get(str(probe.url))
    result = _base_result(probe, resp, started)
    result.size_bytes = len(resp.content)
    if result.outcome is ProbeOutcome.FAILED:
        return result  # challenge or parked page: nothing to parse
    base = str(resp.url)
    parser = _LinkParser()
    parser.feed(resp.text)
    endpoints = extract_endpoints(resp.text, base)
    scanned: list[str] = []
    origin = urlsplit(base).netloc
    for src in parser.scripts:
        script_url = urljoin(base, src)
        if urlsplit(script_url).netloc != origin or len(scanned) >= MAX_SCRIPTS:
            continue
        try:
            script = client.get(script_url)
        except (httpx.HTTPError, RobotsDisallowedError):
            continue
        scanned.append(script_url)
        if script.is_success:
            endpoints.extend(
                e for e in extract_endpoints(script.text[: SNIFF_BYTES * 8], base) if e not in endpoints
            )
    result.details = {
        "title": parser.title,
        "endpoint_candidates": endpoints[:MAX_LIST],
        "keyword_links": keyword_links(parser.links, probe.keywords, base),
        "scripts_scanned": scanned,
    }
    return result


def probe_arcgis(client: PoliteClient, probe: ArcgisProbe) -> ProbeResult:
    started = time.monotonic()
    url = str(probe.url).rstrip("/")
    resp = client.get(url, params={"f": "json"})
    result = _base_result(probe, resp, started)
    if result.outcome is ProbeOutcome.FAILED:
        return result
    try:
        catalog = resp.json()
    except ValueError as exc:
        result.outcome = ProbeOutcome.FAILED
        result.error = f"not JSON: {exc}"
        return result
    services = [f"{s.get('name')} ({s.get('type')})" for s in catalog.get("services", [])]
    folders = list(catalog.get("folders", []))
    for folder in folders[:15]:
        try:
            sub = client.get(f"{url}/{folder}", params={"f": "json"})
            if sub.is_success:
                services.extend(f"{s.get('name')} ({s.get('type')})" for s in sub.json().get("services", []))
        except (httpx.HTTPError, ValueError, RobotsDisallowedError):
            continue
    result.details = {
        "current_version": catalog.get("currentVersion"),
        "folders": folders[:MAX_LIST],
        "services": services[:MAX_LIST],
    }
    return result


# Lists inputs, selects and buttons so the next run can pick selectors for `actions`.
_CONTROLS_JS = """() => Array.from(document.querySelectorAll('input, select, textarea, button'))
    .slice(0, 80).map(e => ({
        tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || null, id: e.id || null,
        placeholder: e.placeholder || null, text: (e.innerText || '').trim().slice(0, 40) || null}))"""


def probe_browser(probe: BrowserProbe, user_agent: str, wait_s: float = 15.0) -> ProbeResult:
    """Open the page in headless Chromium and record data-looking network responses."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ProbeResult(
            probe.kind,
            str(probe.url),
            ProbeOutcome.SKIPPED,
            error="playwright not installed (uv sync --extra discovery)",
        )
    captured: list[dict[str, Any]] = []
    action_errors: list[str] = []

    def on_response(response: Any) -> None:
        ctype = response.headers.get("content-type", "")
        rtype = response.request.resource_type
        if rtype in {"xhr", "fetch"} or any(t in ctype for t in ("json", "protobuf", "xml", "geo")):
            if rtype in {"image", "stylesheet", "font"}:
                return
            captured.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "content_type": ctype,
                    "resource_type": rtype,
                }
            )

    started = time.monotonic()
    executable = os.environ.get("DCLH_CHROMIUM_PATH") or None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(user_agent=user_agent, locale="pl-PL")
            page.on("response", on_response)
            page.goto(str(probe.url), wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(wait_s * 1000)
            title = page.title()
            controls = page.evaluate(_CONTROLS_JS)
            for step in probe.actions:
                try:
                    locator = page.locator(step.selector).first
                    if step.action == "fill":
                        locator.fill(step.value or "", timeout=5_000)
                    elif step.action == "press":
                        locator.press(step.value or "Enter", timeout=5_000)
                    else:
                        locator.click(timeout=5_000)
                    page.wait_for_timeout(3000)
                except Exception as exc:  # an action that misses must not stop the capture
                    action_errors.append(f"{step.action} {step.selector}: {type(exc).__name__}"[:200])
            browser.close()
    except Exception as exc:
        return ProbeResult(
            probe.kind, str(probe.url), ProbeOutcome.FAILED, error=f"{type(exc).__name__}: {exc}"[:400]
        )
    return ProbeResult(
        probe.kind,
        str(probe.url),
        ProbeOutcome.OK,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        details={
            "title": title,
            "data_requests": captured[:MAX_LIST],
            "controls": controls[:MAX_LIST],  # visible inputs and buttons, to pick selectors for actions
            "action_errors": action_errors,
        },
    )


def run_probe(client: PoliteClient, probe: Probe, *, capture_network: bool) -> ProbeResult:
    try:
        if isinstance(probe, OgcProbe):
            return probe_ogc(client, probe)
        if isinstance(probe, HttpProbe):
            return probe_http(client, probe)
        if isinstance(probe, PageProbe):
            return probe_page(client, probe)
        if isinstance(probe, ArcgisProbe):
            return probe_arcgis(client, probe)
        if not capture_network:
            return ProbeResult(
                probe.kind, str(probe.url), ProbeOutcome.SKIPPED, error="use --capture-network"
            )
        if not client.allowed(str(probe.url)):
            raise RobotsDisallowedError(str(probe.url))
        return probe_browser(probe, client.user_agent)
    except RobotsDisallowedError:
        return ProbeResult(probe.kind, str(probe.url), ProbeOutcome.SKIPPED, error="disallowed by robots.txt")
    except httpx.HTTPError as exc:
        return ProbeResult(
            probe.kind, str(probe.url), ProbeOutcome.FAILED, error=f"{type(exc).__name__}: {exc}"[:400]
        )


def classify(spec: SourceSpec, results: list[ProbeResult]) -> SourceStatus:
    if spec.plan != "mvp" or not results:
        return SourceStatus.NOT_CHECKED
    ok = [r for r in results if r.outcome is ProbeOutcome.OK]
    failed = [r for r in results if r.outcome is ProbeOutcome.FAILED]
    if not ok:
        return SourceStatus.FAILED
    data_ok = any(r.kind in DATA_PROBE_KINDS for r in ok)
    return SourceStatus.VERIFIED if data_ok and not failed else SourceStatus.PARTIAL


def discover(
    specs: list[SourceSpec],
    client: PoliteClient,
    *,
    capture_network: bool = False,
    ran_from: str = "unknown",
) -> DiscoveryReport:
    started = datetime.now(UTC)
    sources: list[SourceDiscovery] = []
    for spec in specs:
        results = (
            [run_probe(client, p, capture_network=capture_network) for p in spec.probes]
            if spec.plan == "mvp"
            else []
        )
        sources.append(
            SourceDiscovery(spec.id, spec.name, spec.plan, classify(spec, results), results, spec.notes)
        )
    return DiscoveryReport(
        started_at=started.isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        user_agent=client.user_agent,
        ran_from=ran_from,
        sources=sources,
    )


# --------------------------------------------------------------------------- reporting


def _probe_summary(r: ProbeResult) -> str:
    bits = [f"{r.kind}: {r.outcome.value}"]
    if r.status_code:
        bits.append(str(r.status_code))
    if r.kind == "ogc" and r.details:
        bits.append(f"{r.details.get('version')} · {r.details.get('layer_count')} layers")
        if r.details.get("count_default"):
            bits.append(f"CountDefault {r.details['count_default']}")
    if r.kind == "page" and r.details:
        bits.append(f"{len(r.details.get('endpoint_candidates', []))} endpoint candidates")
    if r.kind == "browser" and r.details:
        bits.append(f"{len(r.details.get('data_requests', []))} data requests")
    if r.error:
        bits.append(r.error[:80].replace("|", "/"))
    return " · ".join(bits)


def _redirects(report: DiscoveryReport) -> list[tuple[str, ProbeResult]]:
    return [
        (s.source_id, r)
        for s in report.sources
        for r in s.probes
        if r.redirect_status and r.final_url != r.url
    ]


def render_markdown(report: DiscoveryReport) -> str:
    lines = [
        f"Run: {report.started_at[:16].replace('T', ' ')} UTC · from: {report.ran_from}",
        "",
        "| Status | Source | Plan | Probes |",
        "|---|---|---|---|",
    ]
    for s in report.sources:
        probes = "<br>".join(_probe_summary(r) for r in s.probes) or "–"
        lines.append(f"| {STATUS_ICON[s.status]} | `{s.source_id}` {s.name} | {s.plan} | {probes} |")
    redirects = _redirects(report)
    if redirects:
        # Not applied automatically: a redirect can end on a parked or challenge page (see Enea, 2026-10-10).
        lines += ["", "### Przekierowania do przeglądu (nie zastosowane automatycznie)", ""]
        lines += ["| Source | Status | Z | Na | Uwaga |", "|---|---|---|---|---|"]
        for source_id, r in redirects:
            cross = urlsplit(r.url).netloc != urlsplit(r.final_url or "").netloc
            note = "**inna domena**: sprawdzić, czy to nie strona parkingowa" if cross else "ta sama domena"
            lines.append(f"| `{source_id}` | {r.redirect_status} | {r.url} | {r.final_url} | {note} |")
    return "\n".join(lines) + "\n"


BEGIN_MARK = "<!-- discovery:begin -->"
END_MARK = "<!-- discovery:end -->"


def update_sources_doc(doc: Path, table: str) -> None:
    """Replace the generated block between the discovery markers in docs/SOURCES.md."""
    text = doc.read_text(encoding="utf-8")
    start, end = text.find(BEGIN_MARK), text.find(END_MARK)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{doc} has no {BEGIN_MARK} … {END_MARK} block")
    new = text[: start + len(BEGIN_MARK)] + "\n" + table + text[end:]
    doc.write_text(new, encoding="utf-8")


def save_report(report: DiscoveryReport, out_dir: Path) -> tuple[Path, Path]:
    """Write `discovery-<date>.json` and `.md` into `out_dir`; returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Timestamped so that repeated runs on the same day don't overwrite each other.
    stem = f"discovery-{report.started_at[:16].replace(':', '')}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(report.to_json(), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
