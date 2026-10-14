"""Test ride: how many parcels of the scored sites expose a land register (KW) number in public EGiB services.

EKW itself cannot be searched by parcel and is manual only (brief section 2.3). Some powiats publish the KW
number in the GetFeatureInfo answer of their EGiB WMS, which the national KIEG service
(integracja.gugik.gov.pl) relays. This probe measures that coverage for the parcels behind the sites
already in the database:

1. ULDK `GetParcelById` gives the parcel geometry; we query a point inside it (point on surface).
2. KIEG WMS 1.1.1 `GetFeatureInfo` at that point, in EPSG:2180 (WMS 1.1.1 keeps x = easting, y = northing).
3. A KW number is `AAAA/NNNNNNNN/C`: a 4-character court department code (e.g. WA1P), 8 digits, a check digit.

Nothing is written to the database. The KIEG endpoint and layer name are unverified until this runs from
Poland (docs/SOURCES.md); the report keeps a raw sample per powiat so the answer format can be checked by eye.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from shapely import wkt

from dclh_pipeline.http import PoliteClient

ULDK_URL = "https://uldk.gugik.gov.pl/"
KIEG_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
KIEG_LAYER = "dzialki"
INFO_FORMATS = ("text/html", "text/plain")
SAMPLE_CHARS = 1500

# Court department code: 2 letters, a digit, a letter (WA1P, PO1P, KR1P, GL1G...).
KW_RE = re.compile(r"\b([A-Z]{2}\d[A-Z])\s*/\s*(\d{8})\s*/\s*(\d)\b")

# Statuses, in report order.
FOUND = "kw"  # a KW number in the answer
NO_KW = "answer_without_kw"  # the powiat answered, but without a KW number
EMPTY = "no_answer"  # empty answer or service exception: powiat not federated or nothing at the point
ERROR = "error"  # ULDK or KIEG request failed
STATUSES = (FOUND, NO_KW, EMPTY, ERROR)


@dataclass(frozen=True)
class ParcelResult:
    parcel_id: str
    site_id: str
    status: str
    kw: str | None = None
    note: str = ""
    sample: str = ""  # start of the raw answer, for the report


def find_kw(text: str) -> list[str]:
    """Distinct KW numbers in the text, in order, normalised to `AAAA/NNNNNNNN/C`."""
    found = {f"{court}/{number}/{check}": None for court, number, check in KW_RE.findall(text.upper())}
    return list(found)


def powiat_of(parcel_id: str) -> str:
    """The 4-digit powiat TERYT code at the start of a parcel id (142104_2.0013.562/2 -> 1421)."""
    return parcel_id[:4]


def parcel_point(client: PoliteClient, parcel_id: str) -> tuple[float, float]:
    """(easting, northing) in EPSG:2180 of a point guaranteed inside the parcel, from ULDK."""
    resp = client.get(
        ULDK_URL, params={"request": "GetParcelById", "id": parcel_id, "result": "geom_wkt", "srid": "2180"}
    )
    lines = [line for line in resp.text.splitlines() if line.strip()]
    if not lines or lines[0].strip() != "0" or len(lines) < 2:
        raise ValueError(f"ULDK: {' '.join(lines)[:200] or 'pusta odpowiedź'}")
    point = wkt.loads(lines[1].split(";")[-1]).representative_point()
    return point.x, point.y


def featureinfo(client: PoliteClient, x: float, y: float) -> tuple[str, str]:
    """KIEG GetFeatureInfo at (x, y) in EPSG:2180. Returns (format, body); "" = no answer."""
    d = 5.0
    base = {
        "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
        "layers": KIEG_LAYER, "query_layers": KIEG_LAYER, "styles": "", "srs": "EPSG:2180",
        "bbox": f"{x - d},{y - d},{x + d},{y + d}", "width": "101", "height": "101",
        "x": "50", "y": "50", "feature_count": "5",
    }  # fmt: skip
    for fmt in INFO_FORMATS:
        resp = client.get(KIEG_URL, params={**base, "info_format": fmt})
        body = resp.text.strip()
        if resp.is_success and body and "ServiceException" not in body[:400]:
            return fmt, body
    return INFO_FORMATS[-1], ""


def probe_parcel(client: PoliteClient, parcel_id: str, site_id: str) -> ParcelResult:
    """Never raises: a failure becomes an `error` row, so one bad parcel never stops the run."""
    try:
        x, y = parcel_point(client, parcel_id)
        fmt, body = featureinfo(client, x, y)
    except Exception as exc:
        return ParcelResult(parcel_id, site_id, ERROR, note=f"{type(exc).__name__}: {exc}"[:200])
    if not body:
        return ParcelResult(parcel_id, site_id, EMPTY)
    numbers = find_kw(body)
    status = FOUND if numbers else NO_KW
    note = fmt if len(numbers) <= 1 else f"{fmt}; {len(numbers)} KW numbers at the point"
    return ParcelResult(
        parcel_id, site_id, status, kw=numbers[0] if numbers else None, note=note, sample=body[:SAMPLE_CHARS]
    )


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "–"


def render_report(results: list[ParcelResult], *, sites: int, stamp: str) -> str:
    """Markdown: overall coverage, coverage per powiat, then one raw sample per powiat and status."""
    total = len(results)
    counts = Counter(r.status for r in results)
    out = [
        f"# KW coverage probe ({stamp})",
        "",
        f"Sites: {sites} · parcels probed: {total}",
        "",
        "| Status | Parcels | Share |",
        "|---|---|---|",
        *(f"| {s} | {counts[s]} | {_pct(counts[s], total)} |" for s in STATUSES),
        "",
        "## Per powiat",
        "",
        "| Powiat | Parcels | KW found | Answer without KW | No answer | Error |",
        "|---|---|---|---|---|---|",
    ]
    by_powiat: dict[str, Counter[str]] = {}
    for r in results:
        by_powiat.setdefault(powiat_of(r.parcel_id), Counter())[r.status] += 1
    for powiat, c in sorted(by_powiat.items()):
        n = sum(c.values())
        found = f"{c[FOUND]} ({_pct(c[FOUND], n)})"
        out.append(f"| {powiat} | {n} | {found} | {c[NO_KW]} | {c[EMPTY]} | {c[ERROR]} |")
    sites_with_kw = len({r.site_id for r in results if r.status == FOUND})
    sites_probed = len({r.site_id for r in results})
    out += ["", f"Sites with at least one KW number: {sites_with_kw} of {sites_probed}", "", "## Samples", ""]
    for key, r in _first_per(results, lambda r: (powiat_of(r.parcel_id), r.status)).items():
        if not (r.sample or r.note):
            continue
        out += [f"### {key[0]} · {key[1]} · {r.parcel_id}", "", f"note: {r.note}" if r.note else ""]
        if r.sample:
            out += ["```", r.sample, "```"]
        out.append("")
    return "\n".join(out)


def _first_per(
    results: Iterable[ParcelResult], key: Callable[[ParcelResult], tuple[str, str]]
) -> dict[tuple[str, str], ParcelResult]:
    first: dict[tuple[str, str], ParcelResult] = {}
    for r in results:
        first.setdefault(key(r), r)
    return first
