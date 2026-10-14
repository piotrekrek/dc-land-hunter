"""Targeted follow-up to `dclh discover` for the sources that are still open (docs/SOURCES.md).

`dclh discover` only says "reachable". This asks the next question for four sources and prints a verdict for
each:

1. bdot10k: the WFS has one layer (`BDOT10k_powiaty`). Does a feature carry a download link per powiat?
2. kimpzp (already verified, a zoning route that could replace `ru`): is there a WFS next to the WMS, and
   what does the WMS answer for a point inside a known plan area (Reguły)?
3. isok: which WFS, WMS or ATOM services does the INSPIRE page list, and what layers do they offer?
4. ru: the site is an Angular app whose data calls never showed in the browser capture. Which API addresses
   are in the JavaScript of its federated modules?

Run it from a machine in Poland: `dclh discover-followup`. Every section catches its own errors, so one dead
service never hides the others. Nothing here is used by a connector until the answer is written to
docs/SOURCES.md.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from defusedxml import ElementTree
from pyproj import Transformer
from shapely import wkt

from dclh_pipeline.config import OgcProbe
from dclh_pipeline.discovery import extract_endpoints, probe_ogc
from dclh_pipeline.http import PoliteClient

BDOT_WFS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k"
BDOT_LAYER = "ms:BDOT10k_powiaty"
KIMPZP_URL = (
    "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
)
KIMPZP_LAYERS = ("wektor-pow", "plany")
ISOK_INSPIRE = "https://wody.isok.gov.pl/inspire.html"
RU_BASE = "https://rejestr-urbanistyczny.gov.pl"
RU_REMOTES = ("published-fe", "published-details-fe", "repo-fe")
ULDK_URL = "https://uldk.gugik.gov.pl/"
# A parcel inside the MPZP "Regulska" area (tests/golden/sites.yaml). The village centre is the fallback.
REGULY_PARCEL = "142104_2.0013.562/2"
REGULY_CENTRE = (20.863056, 52.173889)  # lon, lat (unverified village centre)

INFO_FORMATS = ("application/json", "text/xml", "text/plain")
MAX_JS_FILES = 8
MAX_JS_BYTES = 2_000_000
MAX_SHOWN = 30
_URL_RE = re.compile(r"https?://[^\s\"'`<>\\)]+")
_API_PATH_RE = re.compile(
    r"[\"'`](/[A-Za-z0-9_\-./{}]*(?:api|ows|wfs|wms|geoserver|rest|graphql)[A-Za-z0-9_\-./{}]*)[\"'`]"
)
_LINK_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_SERVICE_RE = re.compile(r"wfs|wms|atom|\.xml|\.zip|\.gml|ows", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------- 1. bdot10k


def flatten_features(xml: bytes, limit: int = 3) -> list[dict[str, str]]:
    """The first `limit` features of a WFS GetFeature answer as {field: text}, leaf elements only."""
    root = ElementTree.fromstring(xml)
    members = [el for el in root if _local(el.tag) in ("member", "featureMember", "featureMembers")]
    features: list[dict[str, str]] = []
    for member in members:
        for feature in list(member) or [member]:
            row = {
                _local(el.tag): el.text.strip()[:160]
                for el in feature.iter()
                if el.text and el.text.strip() and len(el) == 0
            }
            if row:
                features.append(row)
    return features[:limit]


def link_fields(features: list[dict[str, str]]) -> dict[str, str]:
    """Fields whose value is a web address or points at a file to download."""
    found: dict[str, str] = {}
    for row in features:
        for name, value in row.items():
            if value.lower().startswith(("http://", "https://")) or re.search(
                r"\.(zip|gml|shp|7z)\b", value, re.I
            ):
                found.setdefault(name, value)
    return found


def bdot10k(client: PoliteClient) -> list[str]:
    params = {
        "service": "WFS",
        "request": "GetFeature",
        "version": "2.0.0",
        "typeNames": BDOT_LAYER,
        "count": "3",
    }
    resp = client.get(BDOT_WFS, params=params)
    if not resp.is_success:
        return [f"GetFeature answered HTTP {resp.status_code}", "Verdict: no usable index"]
    features = flatten_features(resp.content)
    if not features:
        return ["GetFeature returned no features", "Verdict: index empty or unreadable"]
    lines = [f"fields: {sorted(features[0])}"]
    lines += [f"feature {i + 1}: {row}" for i, row in enumerate(features)]
    links = link_fields(features)
    lines.append(
        f"Verdict: download link in field '{next(iter(links))}': {next(iter(links.values()))}"
        if links
        else "Verdict: no download link in the index; the per-powiat files must be found another way"
    )
    return lines


# --------------------------------------------------------------------------- 2. kimpzp


def _ogc_summary(client: PoliteClient, url: str, service: str) -> tuple[bool, list[str]]:
    """GetCapabilities of one service. Never raises: a dead service is a line in the report."""
    try:
        result = probe_ogc(client, OgcProbe(kind="ogc", service=service, url=url))
    except Exception as exc:
        return False, [f"{service} GetCapabilities: {type(exc).__name__}: {str(exc)[:200]}"]
    if result.outcome.value != "ok":
        reason = f"{result.outcome.value} (HTTP {result.status_code}) {result.error or ''}".strip()
        return False, [f"{service} GetCapabilities: {reason}"]
    layers = [str(name) for name in result.details.get("layers", [])]
    return True, [f"{service} GetCapabilities ok, {len(layers)} layers: {layers[:MAX_SHOWN]}"]


def reguly_point(client: PoliteClient) -> tuple[tuple[float, float], str]:
    """Centre of a parcel in the Reguły plan area from ULDK, else the village centre: ((lon, lat), note)."""
    try:
        resp = client.get(
            ULDK_URL,
            params={"request": "GetParcelById", "id": REGULY_PARCEL, "result": "geom_wkt", "srid": "2180"},
        )
        text = [line for line in resp.text.splitlines() if line.strip()][-1]
        geom = wkt.loads(text.split(";")[-1])
        x, y = geom.centroid.x, geom.centroid.y
        lon, lat = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True).transform(x, y)
        return (lon, lat), f"centre of parcel {REGULY_PARCEL} (ULDK)"
    except Exception as exc:
        return REGULY_CENTRE, f"village centre (ULDK failed: {type(exc).__name__})"


def kimpzp_featureinfo(client: PoliteClient, lon: float, lat: float, layer: str) -> list[str]:
    """WMS 1.1.1 GetFeatureInfo. Its axis order is fixed (x, y): no EPSG:2180 northing-first trap."""
    d = 0.0005
    base = {
        "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
        "layers": layer, "query_layers": layer, "styles": "", "srs": "EPSG:4326",
        "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}", "width": "101", "height": "101",
        "x": "50", "y": "50", "feature_count": "5",
    }  # fmt: skip
    for fmt in INFO_FORMATS:
        resp = client.get(KIMPZP_URL, params={**base, "info_format": fmt})
        body = resp.text.strip()
        if resp.is_success and body and "ServiceException" not in body[:400]:
            return [f"layer {layer}, {fmt}: HTTP {resp.status_code}, {len(body)} chars", body[:700]]
    return [f"layer {layer}: no answer in {INFO_FORMATS}"]


def kimpzp(client: PoliteClient) -> list[str]:
    wfs_ok, lines = _ogc_summary(client, KIMPZP_URL, "WFS")
    (lon, lat), note = reguly_point(client)
    lines.append(f"query point: {lon:.6f}, {lat:.6f} = {note}")
    answered = False
    for layer in KIMPZP_LAYERS:
        answer = kimpzp_featureinfo(client, lon, lat, layer)
        answered = answered or len(answer) > 1
        lines += answer
    wfs = "available" if wfs_ok else "not available"
    info = "answers" if answered else "gave nothing"
    lines.append(f"Verdict: WFS {wfs}; GetFeatureInfo {info} at Reguły")
    return lines


# --------------------------------------------------------------------------- 3. isok


def service_links(html: str, base_url: str) -> list[str]:
    """Links on the page that look like a service or a bulk download, in page order, without duplicates."""
    found = {urljoin(base_url, h): None for h in _LINK_RE.findall(html) if _SERVICE_RE.search(h)}
    found.update({u: None for u in extract_endpoints(html, base_url)})
    return list(found)


def isok(client: PoliteClient) -> list[str]:
    resp = client.get(ISOK_INSPIRE)
    if not resp.is_success:
        return [f"{ISOK_INSPIRE} answered HTTP {resp.status_code}", "Verdict: INSPIRE page not reachable"]
    links = service_links(resp.text, ISOK_INSPIRE)
    lines = [f"{len(links)} service or file links:"] + [f"  {u}" for u in links[:MAX_SHOWN]]
    working = 0
    services = [
        u
        for u in links
        if re.search(r"wfs|wms", u, re.I) and not urlsplit(u).path.endswith((".html", ".htm"))
    ]
    for url in services[:5]:
        service = "WFS" if re.search(r"wfs", url, re.I) else "WMS"
        bare = urlsplit(url)._replace(query="", fragment="").geturl()
        ok, summary = _ogc_summary(client, bare, service)
        working += ok
        lines += [f"{bare}", *[f"  {s}" for s in summary]]
    lines.append(f"Verdict: {len(links)} links, {working} OGC services answered GetCapabilities")
    return lines


# --------------------------------------------------------------------------- 4. ru


def _is_app_file(name: str) -> bool:
    """Shared libraries carry their package name (`_angular_core.<hash>.js`, `@angular/...`): no API there."""
    return (
        not name.startswith(("_", "@")) and "/@" not in name and not posixpath.basename(name).startswith("_")
    )


def js_files(manifest: Any, base_url: str) -> list[str]:
    """Every string in a module-federation manifest naming an app .js or .mjs file, as an absolute address."""
    names: dict[str, None] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and re.search(r"\.m?js$", node) and _is_app_file(node):
            names.setdefault(urljoin(base_url, node), None)

    walk(manifest)
    return list(names)


def api_hints(source: str) -> tuple[list[str], list[str]]:
    """(addresses on *.gov.pl, quoted paths that mention api/ows/wfs/wms/rest) found in JavaScript source."""
    urls = {
        u.rstrip(".,;") for u in _URL_RE.findall(source) if (urlsplit(u).hostname or "").endswith("gov.pl")
    }
    return sorted(urls), sorted(set(_API_PATH_RE.findall(source)))


def ru(client: PoliteClient) -> list[str]:
    urls: set[str] = set()
    paths: set[str] = set()
    scanned = 0
    lines: list[str] = []
    for remote in RU_REMOTES:
        entry = f"{RU_BASE}/{remote}/remoteEntry.json"
        resp = client.get(entry)
        if not resp.is_success:
            lines.append(f"{remote}: remoteEntry.json HTTP {resp.status_code}")
            continue
        manifest = json.loads(resp.text)
        keys = sorted(manifest) if isinstance(manifest, dict) else type(manifest).__name__
        files = js_files(manifest, entry)[:MAX_JS_FILES]
        lines.append(
            f"{remote}: manifest keys {keys}; {len(files)} app JavaScript files: "
            f"{[f.rsplit('/', 1)[-1] for f in files]}"
        )
        for file in files:
            try:
                js = client.get(file)
            except Exception as exc:  # one bad file (a redirect loop was seen) must not end the section
                lines.append(f"  {file.rsplit('/', 1)[-1]}: {type(exc).__name__}")
                continue
            if js.is_success:
                scanned += 1
                found_urls, found_paths = api_hints(js.content[:MAX_JS_BYTES].decode("utf-8", "replace"))
                urls.update(found_urls)
                paths.update(found_paths)
    lines.append(f"scanned {scanned} files")
    lines += [f"  address: {u}" for u in sorted(urls)[:MAX_SHOWN]] + [
        f"  path: {p}" for p in sorted(paths)[:MAX_SHOWN]
    ]
    lines.append(f"Verdict: {len(urls)} gov.pl addresses and {len(paths)} api-like paths in {scanned} files")
    return lines


# --------------------------------------------------------------------------- run


SECTIONS: tuple[tuple[str, Callable[[PoliteClient], list[str]]], ...] = (
    ("1. bdot10k: does the powiat index carry download links?", bdot10k),
    ("2. kimpzp: WFS next to the WMS, and a GetFeatureInfo at Reguły", kimpzp),
    ("3. isok: services listed on the INSPIRE page", isok),
    ("4. ru: API addresses in the JavaScript of the federated modules", ru),
)


def run(client: PoliteClient, now: datetime | None = None) -> str:
    """Run every section and return a Markdown report. A failing section is reported, never raised."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Discovery follow-up ({stamp})", ""]
    for title, section in SECTIONS:
        out += [f"## {title}", "```"]
        try:
            out += section(client)
        except (
            Exception
        ) as exc:  # a diagnostic tool: whatever goes wrong in one section, the others still run
            out += [f"FAILED: {type(exc).__name__}: {str(exc)[:300]}", "Verdict: could not be checked"]
        out += ["```", ""]
    return "\n".join(out)
