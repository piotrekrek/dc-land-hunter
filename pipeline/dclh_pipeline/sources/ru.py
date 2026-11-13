"""Rejestr Urbanistyczny (RU): plan ogólny zones and MPZP outlines for one search area, from the APP WFS.

Verified 19.10.2026 (docs/SOURCES.md, "Hard filters and zoning"):

- `app-pog/wfs`, layer `app-pog:StrefaPlanistyczna`: plan ogólny zones. `symbol` (SJ, SU, SP…) and the zone
  type in `nazwa` (an xlink to the code list). The strefa gospodarcza is `nazwa = …/strefaGospodarcza`,
  symbol SP.
  A server-side filter on `nazwa` returns nothing, so the client filters (`model/zoning.py`).
- 11.11.2026 (docs/SOURCES.md, "OUZ and zone parameters"): `app-pog/wfs` also has
  `app-pog:ObszarUzupelnieniaZabudowy` (OUZ) and `app-pog:AktPlanowaniaPrzestrzennego` (the plan act with
  its extent), and `StrefaPlanistyczna` carries `maksWysokoscZabudowy`, `maksUdzialPowierzchniZabudowy`,
  `minUdzialPowierzchniBiologicznieCzynnej` and `maksNadziemnaIntensywnoscZabudowy`.
- `app-mpzp/wfs`, layer `app-mpzp:AktPlanowaniaPrzestrzennego`: MPZP outlines only, no designations. Its
  `status` tells a plan in force (`legalForce`) from one in procedure (`elaboration`, `adoption`).

Both answer EPSG:2180 northing-first and return at most 10 features per request whatever `count` asks (the
server's CountDefault), so the connector pages with startIndex over one bbox (a powiat) and checks the total
against numberMatched. The xlink attributes (`status`, `nazwa`) are read with GDAL's
GML_ATTRIBUTES_TO_OGR_FIELDS, as `<field>_href`.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import httpx
import pandas as pd
from pyogrio import read_dataframe

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.planning import PlanRegister, build_plan_register
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.bbox_check import features_near_bbox
from dclh_pipeline.sources.egib import BBox

log = logging.getLogger(__name__)

BASE_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe"
EPSG = 2180
SRS = "urn:ogc:def:crs:EPSG::2180"
PAGE_SIZE = 10  # the server's CountDefault; it never returns more per request
MAX_PAGES = 2000
MAX_SKIPPED = (
    60  # features skipped per layer and powiat (refused or timing out) before the fetch fails instead
)
# A powiat is asked for in tiles of at most this side: the service times out (504) on large areas, and a
# failed tile costs a few pages, not the whole powiat. A request for a smaller area is a single tile.
TILE_M = 30_000.0
# A fixed member date, so identical pages make an identical zip and the store keeps one object.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+|unknown)"')
_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_TIMESTAMP = re.compile(rb'\s+timeStamp="[^"]*"')


def _counts(body: bytes) -> tuple[int, int]:
    """(numberMatched, numberReturned) of one answer."""
    m = _NUMBER_MATCHED.search(body[:4000])
    if m is None or m.group(1) == b"unknown":
        raise ValueError("RU: the response has no numberMatched; cannot check it is complete")
    r = _NUMBER_RETURNED.search(body[:4000])
    return int(m.group(1)), int(r.group(1)) if r else 0


def getfeature_params(layer: str, bbox: BBox, *, start: int, count: int) -> dict[str, str | int]:
    """One page of `layer` inside `bbox` (easting, northing). The bbox is sent northing-first."""
    x0, y0, x1, y1 = bbox
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": layer,
        "srsName": SRS,
        "bbox": f"{y0},{x0},{y1},{x1},{SRS}",
        "count": count,
        "startIndex": start,
    }


def tiles(bbox: BBox, side_m: float = TILE_M) -> list[BBox]:
    """`bbox` cut into equal tiles whose sides are at most `side_m`; one tile when it is small enough."""
    x0, y0, x1, y1 = bbox
    nx = max(1, math.ceil((x1 - x0) / side_m))
    ny = max(1, math.ceil((y1 - y0) / side_m))
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    return [
        (x0 + i * dx, y0 + j * dy, x0 + (i + 1) * dx, y0 + (j + 1) * dy) for j in range(ny) for i in range(nx)
    ]


class _RuWfs(Source):
    """Pages one RU layer over a bbox. Subclasses name the layer and the columns they keep."""

    id: ClassVar[str] = "ru"
    path: ClassVar[str]
    layer: ClassVar[str]
    columns: ClassVar[dict[str, str]]  # output column → GDAL field
    # Output columns whose GDAL field may be absent: GDAL builds the schema from each page, so a page whose
    # features all lack an optional element (a zone with no height limit) has no such field. They read "".
    optional: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, spec: SourceSpec, *, bbox: BBox, page_size: int = PAGE_SIZE) -> None:
        super().__init__(spec)
        x0, y0, x1, y1 = bbox
        self.bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
        self.page_size = page_size

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.path}"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layer": self.layer, "bbox": self.bbox}).encode())
            got, written = 0, 0
            skipped: list[tuple[int, int]] = []  # (tile, feature index)

            for tile_no, tile in enumerate(tiles(self.bbox)):
                start, matched, tile_got = 0, None, 0
                skipped_here = 0

                def keep(body: bytes, tile_no: int = tile_no) -> int:
                    nonlocal matched, got, written, tile_got
                    matched, returned = _counts(body)
                    if returned:
                        _write(archive, f"page_{written:05d}.gml", body)
                        written += 1
                        got += returned
                        tile_got += returned
                    return returned

                for _ in range(MAX_PAGES):
                    body = self._page(client, tile, start, self.page_size)
                    if body is not None:
                        returned = keep(body)
                        start += returned
                        if returned == 0 or start >= (matched or 0):
                            break
                        continue
                    # The server refuses a page, or times out on it, when one feature on it is broken or too
                    # heavy (19.10.2026: one MPZP outline in the 1421 bbox; 25.10.2026: 504 on heavy pages).
                    # Ask that page one feature at a time; skip only the ones that still fail.
                    for i in range(start, start + self.page_size):
                        one = self._page(client, tile, i, 1)
                        if one is None:
                            skipped.append((tile_no, i))
                            skipped_here += 1
                        else:
                            keep(one)
                    if matched is None or len(skipped) > MAX_SKIPPED:
                        raise ValueError(
                            f"RU: the server refused {len(skipped)} features of {self.layer} for bbox {tile}"
                        )
                    start += self.page_size
                    if start >= matched:
                        break
                else:
                    raise ValueError(f"RU: more than {MAX_PAGES} pages of {self.layer} for bbox {tile}")
                if matched is None or tile_got + skipped_here < matched:
                    raise ValueError(
                        f"RU: got {tile_got} of {matched} features of {self.layer} in tile {tile_no}; "
                        "the response is incomplete"
                    )
            if skipped:
                log.warning("RU: skipped %d features of %s at %s", len(skipped), self.layer, skipped)
                _write(archive, "skipped.json", json.dumps(skipped).encode())
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=self.url, content_type="application/zip"
        )

    def _page(self, client: PoliteClient, bbox: BBox, start: int, count: int) -> bytes | None:
        """One answer without its timestamp, or None when the server refuses it (400 "Invalid request") or
        keeps timing out on it (5xx after the client's own retries)."""
        try:
            resp = client.get(self.url, params=getfeature_params(self.layer, bbox, start=start, count=count))
        except httpx.TransportError as exc:  # a timeout or reset that outlasted the client's retries
            log.warning("RU %s: %s at startIndex %d, count %d", self.layer, type(exc).__name__, start, count)
            return None
        if resp.status_code >= 500:
            log.warning(
                "RU %s: HTTP %d at startIndex %d, count %d", self.layer, resp.status_code, start, count
            )
            return None
        if resp.status_code == 400:
            return None
        resp.raise_for_status()
        body = _TIMESTAMP.sub(b"", resp.content, count=1)
        if b"ExceptionReport" in body[:2000]:
            raise ValueError(f"RU WFS returned an exception: {body[:500]!r}")
        return body

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        with zipfile.ZipFile(snapshot.path) as archive:
            request = json.loads(archive.read("request.json"))
            if request["layer"] != self.layer:
                raise ValueError(f"RU: snapshot holds {request['layer']}, not {self.layer}")
            pages = sorted(n for n in archive.namelist() if n.startswith("page_"))
            frames = [self._read_page(archive.read(name)) for name in pages]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return self._empty()
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        # Some features must lie near the bbox; none near means swapped axes or a wrong CRS (`bbox_check`).
        # Fail rather than put plans in the wrong place.
        if not features_near_bbox(table, request["bbox"]):
            raise ValueError(
                f"RU: none of the {len(table)} features lies near the requested bbox; "
                "check the axis order (EPSG:2180, the service answers northing-first)"
            )
        # Plans are drawn by gminas; an invalid ring would break the overlay with the sites.
        invalid = ~table.geometry.is_valid
        table.loc[invalid, "geometry"] = table.geometry[invalid].make_valid()
        return table.drop_duplicates("gml_id").drop(columns="gml_id").reset_index(drop=True)

    def _read_page(self, gml: bytes) -> gpd.GeoDataFrame:
        # GDAL's GML driver needs a file on disk (and writes a .gfs next to it, so use a fresh directory).
        # SWAP_COORDINATES=YES because the service writes northing first.
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "page.gml"
            path.write_bytes(gml)
            raw = read_dataframe(path, SWAP_COORDINATES="YES", GML_ATTRIBUTES_TO_OGR_FIELDS="YES")
        wanted = ["gml_id", *(f for c, f in self.columns.items() if c not in self.optional)]
        missing = [f for f in wanted if f not in raw.columns]
        if missing:
            raise ValueError(f"RU {self.layer}: fields {missing} not found; available: {sorted(raw.columns)}")
        raw = raw[raw.geometry.notna() & ~raw.geometry.is_empty]
        out = gpd.GeoDataFrame(
            {
                "gml_id": raw["gml_id"].astype(str),
                **{
                    c: (raw[f].astype(str) if f in raw.columns else pd.Series("", index=raw.index))
                    for c, f in self.columns.items()
                },
            },
            geometry=raw.geometry,
        )
        return out.set_crs(f"EPSG:{EPSG}", allow_override=True)

    def _empty(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {c: pd.Series(dtype=str) for c in self.columns},
            geometry=gpd.GeoSeries([], dtype="geometry"),
            crs=f"EPSG:{EPSG}",
        )


class RuPlanOgolnyConnector(_RuWfs):
    """Plan ogólny zones: one row per zone with its plan, symbol, zone type and status (code-list hrefs)."""

    path = "app-pog/wfs"
    layer = "app-pog:StrefaPlanistyczna"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "symbol": "symbol",
        "zone_href": "nazwa_href",
        "status_href": "status_href",
        # Zone parameters (checked live around Rzepin, 11.11.2026). Optional: a zone may set none of them.
        "max_height_m": "maksWysokoscZabudowy",
        "max_build_up_pct": "maksUdzialPowierzchniZabudowy",
        "min_pbc_pct": "minUdzialPowierzchniBiologicznieCzynnej",
        "max_intensity": "maksNadziemnaIntensywnoscZabudowy",
    }
    optional: ClassVar[frozenset[str]] = frozenset(
        {"max_height_m", "max_build_up_pct", "min_pbc_pct", "max_intensity"}
    )


class RuOuzConnector(_RuWfs):
    """Obszary uzupełnienia zabudowy (OUZ) of the plans ogólne: one row per area with its plan and status."""

    path = "app-pog/wfs"
    layer = "app-pog:ObszarUzupelnieniaZabudowy"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "status_href": "status_href",
    }


class RuPlanActConnector(_RuWfs):
    """Plan ogólny acts: one row per gmina plan with its extent, title, date it takes effect and the adopting
    resolution (`resolution_href` ends in the document id, e.g. `POG_Rzepin_XXXIV_224_2026_uchwalajaca`)."""

    path = "app-pog/wfs"
    layer = "app-pog:AktPlanowaniaPrzestrzennego"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "title": "tytul",
        "in_force_from": "obowiazujeOd",
        "status_href": "status_href",
        "resolution_href": "dokumentUchwalajacy_href",
    }
    optional: ClassVar[frozenset[str]] = frozenset({"title", "in_force_from", "resolution_href"})


class RuMpzpConnector(_RuWfs):
    """MPZP outlines: one row per plan with its status (code-list href). No designations."""

    path = "app-mpzp/wfs"
    layer = "app-mpzp:AktPlanowaniaPrzestrzennego"
    columns: ClassVar[dict[str, str]] = {
        "plan_id": "przestrzenNazw",
        "local_id": "lokalnyId",
        "status_href": "status_href",
    }


MAX_ACTS_SKIPPED = 5  # acts the server refuses before the fetch fails; each one hides a gmina's plan
_POS_LIST = re.compile(rb"<gml:posList[^>]*>[^<]*</gml:posList>")
_ACT_GMINA = re.compile(r"<app-pog:przestrzenNazw>[^<]*/(\d{6})-POG</")
_ACT_STATUS = re.compile(r'<app-pog:status\s[^>]*xlink:href="[^"]*/([^"/]+)"')
_ACT_TITLE = re.compile(r"<app-pog:tytul>([^<]*)</")
_ACT_FROM = re.compile(r"<app-pog:obowiazujeOd>([^<]*)</")


class RuActRegisterConnector(Source):
    """Every plan ogólny act in the register, nationwide: which gminas have a plan and its status.

    Checked live 13.11.2026: 786 acts, ten per request, no bbox needed. The service cannot leave the extent
    out (`propertyName` is refused) and an act weighs up to 0.5 MB, so the snapshot keeps only the attributes
    (`teryt` = the six digits in `przestrzenNazw`, e.g. `PL.ZIPPZP.10434/080504-POG`; `status`; `tytul`;
    `obowiazujeOd`) as JSON. A short answer fails the fetch: a gmina missing from the list would read as
    "no plan". A few acts the server refuses are skipped with a warning (`MAX_ACTS_SKIPPED`).
    """

    id: ClassVar[str] = "ru_acts"
    url: ClassVar[str] = f"{BASE_URL}/app-pog/wfs"
    layer: ClassVar[str] = "app-pog:AktPlanowaniaPrzestrzennego"
    columns: ClassVar[tuple[str, ...]] = ("teryt", "status", "title", "in_force_from")

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        acts: list[dict[str, str]] = []
        start, matched, skipped = 0, None, 0
        for _ in range(MAX_PAGES):
            body = self._page(client, start, PAGE_SIZE)
            if body is not None:
                matched, returned = _counts(body)
                acts += _act_rows(body)
                start += returned
                if returned == 0 or start >= matched:
                    break
                continue
            # The server refuses a whole page when one act on it is broken (13.11.2026: the page at 570).
            # Ask for that page one act at a time and skip only the acts that still fail.
            for i in range(start, start + PAGE_SIZE):
                if matched is not None and i >= matched:
                    break  # past the end of the list
                one = self._page(client, i, 1)
                if one is None:
                    skipped += 1
                else:
                    matched = _counts(one)[0]
                    acts += _act_rows(one)
            start += PAGE_SIZE
            if matched is None or skipped > MAX_ACTS_SKIPPED:
                raise ValueError(f"RU: the server refused {skipped} plan acts; the list is incomplete")
            if start >= matched:
                break
        if matched is None or len(acts) + skipped != matched:
            raise ValueError(f"RU: got {len(acts)} of {matched} plan acts; the list is incomplete")
        if skipped:
            log.warning("RU: %d of %d plan acts refused; their gminas read as planless", skipped, matched)
        content = json.dumps(acts, ensure_ascii=False, indent=0).encode()
        return store.save(self.id, content, suffix=".json", url=self.url, content_type="application/json")

    def _page(self, client: PoliteClient, start: int, count: int) -> bytes | None:
        """One answer, or None when the server refuses it (400) or keeps failing on it (5xx, timeouts)."""
        params: dict[str, str | int] = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": self.layer,
            "count": count,
            "startIndex": start,
        }
        try:
            resp = client.get(self.url, params=params)
        except httpx.TransportError as exc:
            log.warning("RU acts: %s at startIndex %d, count %d", type(exc).__name__, start, count)
            return None
        if resp.status_code == 400 or resp.status_code >= 500:
            log.warning("RU acts: HTTP %d at startIndex %d, count %d", resp.status_code, start, count)
            return None
        resp.raise_for_status()
        if b"ExceptionReport" in resp.content[:2000]:
            raise ValueError(f"RU WFS returned an exception: {resp.content[:500]!r}")
        return resp.content

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        acts = json.loads(snapshot.path.read_bytes())
        if not acts:
            raise ValueError("RU: the act list holds no plan; the register cannot be empty")
        return pd.DataFrame(acts, columns=list(self.columns))


def load_plan_register(store: SnapshotStore, spec: SourceSpec) -> PlanRegister | None:
    """The gminas with a plan ogólny in force, or None when `dclh fetch ru_acts` has never run."""
    snapshot = store.latest_good(RuActRegisterConnector.id)
    if snapshot is None:
        return None
    return build_plan_register(RuActRegisterConnector(spec).parse(snapshot), snapshot.fetched_at)


def _act_rows(body: bytes) -> list[dict[str, str]]:
    return [_act_row(member) for member in body.split(b"<wfs:member>")[1:]]


def _act_row(member: bytes) -> dict[str, str]:
    """The attributes of one act; the extent (most of the bytes) is dropped first."""
    text = _POS_LIST.sub(b"", member).decode()
    gmina, status = _ACT_GMINA.search(text), _ACT_STATUS.search(text)
    if gmina is None or status is None:
        raise ValueError(f"RU: a plan act has no gmina code or status: {text[:300]!r}")
    title, since = _ACT_TITLE.search(text), _ACT_FROM.search(text)
    return {
        "teryt": gmina.group(1),
        "status": status.group(1),
        "title": title.group(1) if title else "",
        "in_force_from": since.group(1) if since else "",
    }


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)
