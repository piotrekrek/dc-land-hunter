"""GUGiK BDOT10k: residential buildings of one powiat, for the distance-to-housing filter.

Feeds `residential_min_m` and `residential_clear_ha` (hard filter `residential_within_m`, penalty
`residential_100_300m`). Verified 19.10.2026 (docs/SOURCES.md): the WFS layer `ms:BDOT10k_powiaty` gives
each powiat's zip in `URL_GML`
(`https://opendata.geoportal.gov.pl/bdot10k/schemat2021/14/1421_GML.zip`, 18.6 MB). Buildings are in the
member `…__OT_BUBD_A.xml`, EPSG:2180, easting-first. Residential means `funkcjaOgolnaBudynku = "budynki
mieszkalne"`; buildings with `kategoriaIstnienia = "zniszczony"` (ruins) are left out. Buildings under
construction count: they will be lived in.

`fetch()` asks the download server for the zip by its fixed address
(`schemat2021/<voivodeship>/<teryt>_GML.zip`, the URL the index gives) and stores it as it is. Only when that
fails does it look the URL up in the WFS (one feature, filtered by TERYT): on 26.10.2026 that lookup hung for
minutes and failed eight searches while the download server answered at once. `parse()` reads only the
buildings member, straight from the zip.

Forest (`forest` constraint, hard filter `forest_pct_over`) comes from the same zip, member `…__OT_PTLZ_A.xml`
(BDOT10k land cover, "pokrycie terenu: tereny leśne lub zadrzewione"). Only `rodzaj = "las"` counts as forest:
`zadrzewienie` (tree rows, orchards) and `zagajnik` (young scrub) are not forest. Verified 20.10.2026 on the
powiat 1421 zip (1,640 PTLZ features, rodzaj values las / zadrzewienie / zagajnik). The land register class
Ls is not used: KIUG gives it only per parcel, and a point sample cannot measure a share.
"""

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

import geopandas as gpd
import httpx
from pyogrio import read_dataframe

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import POLAND_BOUNDS

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k"
INDEX_LAYER = "ms:BDOT10k_powiaty"
DOWNLOAD_HOST = "opendata.geoportal.gov.pl"
BUILDINGS_MEMBER_SUFFIX = "__OT_BUBD_A.xml"
FOREST_MEMBER_SUFFIX = "__OT_PTLZ_A.xml"
FOREST_FIELD = "rodzaj"
FOREST = "las"
FUNCTION_FIELD = "funkcjaOgolnaBudynku"
DETAIL_FIELD = "funkcjaSzczegolowaBudynku"
STATUS_FIELD = "kategoriaIstnienia"
RESIDENTIAL = "budynki mieszkalne"
DESTROYED = "zniszczony"
EPSG = 2180

log = logging.getLogger(__name__)

_URL_GML = re.compile(r"<ms:URL_GML>\s*([^<\s]+)\s*</ms:URL_GML>")
_TERYT = re.compile(r"\d{4}")


def index_params(teryt: str) -> dict[str, str]:
    """GetFeature for one powiat's row of the download index, attributes only."""
    fes = (
        '<Filter xmlns="http://www.opengis.net/fes/2.0"><PropertyIsEqualTo>'
        f"<ValueReference>TERYT</ValueReference><Literal>{teryt}</Literal>"
        "</PropertyIsEqualTo></Filter>"
    )
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": INDEX_LAYER,
        "propertyName": "ms:TERYT,ms:URL_GML",
        "FILTER": fes,
    }


class Bdot10kConnector(Source):
    id: ClassVar[str] = "bdot10k"

    def __init__(self, spec: SourceSpec, *, teryt: str) -> None:
        super().__init__(spec)
        teryt = teryt.zfill(4)
        if not _TERYT.fullmatch(teryt):
            raise ValueError(f"BDOT10k: a powiat TERYT has 4 digits, got {teryt!r}")
        self.teryt = teryt

    @property
    def direct_url(self) -> str:
        """Where the index puts this powiat's zip: the voivodeship (first two digits) is the folder."""
        return f"https://{DOWNLOAD_HOST}/bdot10k/schemat2021/{self.teryt[:2]}/{self.teryt}_GML.zip"

    def zip_url(self, client: PoliteClient) -> str:
        resp = client.get(WFS_URL, params=index_params(self.teryt))
        resp.raise_for_status()
        urls = _URL_GML.findall(resp.text)
        if len(urls) != 1:
            raise ValueError(f"BDOT10k: expected one URL_GML for powiat {self.teryt}, got {len(urls)}")
        url = str(urls[0])
        # Only follow a link to the GUGiK download server, and only to this powiat's file.
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != DOWNLOAD_HOST
            or not parts.path.endswith(f"/{self.teryt}_GML.zip")
        ):
            raise ValueError(f"BDOT10k: unexpected download URL for powiat {self.teryt}: {url}")
        return url

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        tmp_dir = store.root / self.id / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=".zip", delete=False) as fh:
            dest = Path(fh.name)
        url = self.direct_url
        try:
            client.download(url, dest)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            log.warning("BDOT10k: the fixed address failed (%s); looking the URL up in the index", exc)
            url = self.zip_url(client)
            client.download(url, dest)
        if not zipfile.is_zipfile(dest):
            dest.unlink()
            raise ValueError(f"BDOT10k: {url} is not a zip file")
        return store.save_file(self.id, dest, suffix=".zip", url=url, content_type="application/zip")

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        member = _member(snapshot, BUILDINGS_MEMBER_SUFFIX)
        # Easting-first with srsName="EPSG:2180"; SWAP_COORDINATES=NO states that instead of relying on GDAL.
        raw = read_dataframe(
            f"/vsizip/{snapshot.path}/{member}",
            columns=[FUNCTION_FIELD, DETAIL_FIELD, STATUS_FIELD],
            SWAP_COORDINATES="NO",
        )
        missing = [f for f in (FUNCTION_FIELD, DETAIL_FIELD, STATUS_FIELD) if f not in raw.columns]
        if missing:
            raise ValueError(f"BDOT10k: fields {missing} not found; available: {sorted(raw.columns)}")
        keep = (
            (raw[FUNCTION_FIELD] == RESIDENTIAL)
            & (raw[STATUS_FIELD] != DESTROYED)
            & raw.geometry.notna()
            & ~raw.geometry.is_empty
        )
        table = gpd.GeoDataFrame(
            {
                "kind": raw.loc[keep, DETAIL_FIELD].astype(str).to_numpy(),
                "status": raw.loc[keep, STATUS_FIELD].astype(str).to_numpy(),
            },
            geometry=raw.geometry[keep].to_numpy(),
            crs=f"EPSG:{EPSG}",
        )
        _check_bounds(table)
        return table

    def parse_forest(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        """Forest polygons (`rodzaj = las`) of the powiat, in EPSG:2180. Empty when the powiat has none."""
        member = _member(snapshot, FOREST_MEMBER_SUFFIX)
        raw = read_dataframe(
            f"/vsizip/{snapshot.path}/{member}", columns=[FOREST_FIELD], SWAP_COORDINATES="NO"
        )
        if FOREST_FIELD not in raw.columns:
            raise ValueError(
                f"BDOT10k: field {FOREST_FIELD!r} not found in PTLZ; available: {sorted(raw.columns)}"
            )
        keep = (raw[FOREST_FIELD] == FOREST) & raw.geometry.notna() & ~raw.geometry.is_empty
        table = gpd.GeoDataFrame(
            {"kind": raw.loc[keep, FOREST_FIELD].astype(str).to_numpy()},
            geometry=raw.geometry[keep].to_numpy(),
            crs=f"EPSG:{EPSG}",
        )
        _check_bounds(table)
        return table


def _member(snapshot: Snapshot, suffix: str) -> str:
    """The one member of the powiat zip whose name ends with `suffix`."""
    with zipfile.ZipFile(snapshot.path) as archive:
        members = [n for n in archive.namelist() if n.endswith(suffix)]
    if len(members) != 1:
        raise ValueError(f"BDOT10k: expected one *{suffix} in the zip, found {members}")
    return members[0]


def _check_bounds(table: gpd.GeoDataFrame) -> None:
    if table.empty:
        return
    minx, miny, maxx, maxy = table.total_bounds
    x0, y0, x1, y1 = POLAND_BOUNDS
    if minx < x0 or miny < y0 or maxx > x1 or maxy > y1:
        raise ValueError(
            f"BDOT10k coordinates outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); "
            "check the axis order (EPSG:2180)"
        )
