"""Manual imports for the Phase 3 grid sources: the fallback when no machine-readable endpoint is verified.

Enea, Energa and Tauron publish capacity in interactive maps, and the PSE lists come as XLSX and PDF
files. Until a data endpoint is verified in `docs/SOURCES.md`, an owner (or a person from the team) saves
the published file as a CSV or XLSX with the canonical columns below, and imports it with
`dclh import-manual <source> <file>`. Nothing here downloads anything: the connector only stores the file
as a snapshot and validates it.

DSO capacity (`enea`, `energa`, `tauron`), one DSO per file, columns:
    dso, gpz_name, published_mw, as_of, note
    `published_mw` may be blank (not published); `as_of` is the date the DSO published the value
    (YYYY-MM-DD).

PSE point lists (`pse_closed`, `pse_odb`, `pse_avail`), columns:
    name, as_of, and either x_2180 + y_2180 (metres, EPSG:2180) or lat + lon (WGS84)
    `pse_closed` and `pse_odb` also take PSE's own XLSX as published (`sources/pse.py`). Those lists have no
    coordinates: the rows keep an empty geometry and are located by station name later. What is stored is
    the reduced table (as CSV), never PSE's file, so the applicant names in the art. 7 ust. 8l list are not
    kept anywhere. `pse_avail` also takes PSE's PDF as published: its planned stations get approximate
    coordinates from `data/manual/pse_planned_station_coords.csv`.

Headers are matched case-insensitively with spaces turned into underscores. Rows that break a rule fail
the whole import with the row number, so a partly-read file never reaches the scores.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.gpz_match import normalise_name
from dclh_pipeline.model.grid import parse_osm_ref
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import EPSG, POLAND_BOUNDS
from dclh_pipeline.sources.pse import read_pse_avail, read_pse_closed, read_pse_odb

CAPACITY_COLUMNS = ("dso", "gpz_name", "published_mw", "as_of")
POINT_COLUMNS = ("name", "as_of")
_WGS84_TO_2180 = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)


def read_table(path: Path, *, comment: str | None = None) -> pd.DataFrame:
    """Read a CSV (separator sniffed: `,` or `;`) or the first XLSX sheet as text.

    Headers are normalised to lower case with underscores.
    """
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        raw = pd.read_excel(path, sheet_name=0, dtype=str, keep_default_na=False)
    else:
        raw = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            sep=None,
            engine="python",
            encoding="utf-8-sig",
            comment=comment,
        )
    raw.columns = [str(c).strip().lower().replace(" ", "_") for c in raw.columns]
    return raw.fillna("").map(lambda value: str(value).strip())


def _require(raw: pd.DataFrame, columns: tuple[str, ...], what: str) -> None:
    missing = [c for c in columns if c not in raw.columns]
    if missing:
        raise ValueError(f"{what}: missing columns {missing}; found {list(raw.columns)}")


def _row_error(row: int, message: str) -> ValueError:
    return ValueError(f"row {row + 2}: {message}")  # +2: the header line, and 1-based numbering


_EXCEL_MIDNIGHT = re.compile(r"(\d{4}-\d{2}-\d{2})[ T]00:00:00(\.0+)?")


def _parse_date(text: str, row: int) -> pd.Timestamp:
    if not text:
        raise _row_error(row, "as_of is empty (use YYYY-MM-DD)")
    if m := _EXCEL_MIDNIGHT.fullmatch(text):
        text = m.group(1)  # an Excel date cell, read as text, carries a midnight time
    try:
        return pd.Timestamp(date.fromisoformat(text))
    except ValueError as exc:
        raise _row_error(row, f"as_of {text!r} is not a date in YYYY-MM-DD form") from exc


class ManualFileSource(Source):
    """A source whose data is a file the owner saved by hand. There is nothing to download."""

    id: ClassVar[str]

    def __init__(self, spec: SourceSpec, path: Path) -> None:
        super().__init__(spec)
        self.path = path

    def store_file(self, store: SnapshotStore) -> Snapshot:
        """Copy the file into the snapshot store. `save` copies the bytes: `save_file` would move the
        owner's original out of data/manual."""
        return store.save(
            self.id,
            self.path.read_bytes(),
            suffix=self.path.suffix.lower() or ".csv",
            url=f"manual:{self.path.name}",
        )

    def import_file(self, store: SnapshotStore) -> tuple[Snapshot, pd.DataFrame]:
        """Validate every row first, and store the file only when all of them are valid.

        A rejected file leaves no trace in the store. An accepted one is marked good, so it can serve as the
        fallback snapshot like any fetched one.
        """
        probe = Snapshot(
            source_id=self.id,
            fetched_at=datetime.now(UTC),
            sha256="",
            path=self.path,
            size_bytes=self.path.stat().st_size,
        )
        table = self.parse(probe)
        return store.mark_good(self.store_file(store)), table

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        """The client is not used: manual sources never go to the network."""
        return self.store_file(store)


class ManualCapacitySource(ManualFileSource):
    """One DSO's published capacity per GPZ, from a file. Subclasses set `id` to the DSO source id."""

    id: ClassVar[str]

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        raw = read_table(snapshot.path)
        _require(raw, CAPACITY_COLUMNS, f"{self.id} capacity file")
        rows: list[dict[str, object]] = []
        for i, rec in enumerate(raw.to_dict("records")):
            dso = str(rec["dso"]).lower()
            if dso != self.id:
                raise _row_error(i, f"dso is {dso!r}; this file is for {self.id!r}")
            name = str(rec["gpz_name"])
            if not normalise_name(name):
                raise _row_error(i, "gpz_name is empty")
            rows.append(
                {
                    "dso": self.id,
                    "gpz_name": name,
                    "published_mw": _parse_mw(str(rec["published_mw"]), i),
                    "as_of": _parse_date(str(rec["as_of"]), i),
                    "note": str(rec.get("note", "")),
                }
            )
        table = pd.DataFrame(rows, columns=["dso", "gpz_name", "published_mw", "as_of", "note"])
        # "GPZ Piaski" and "Piaski" are the same station to the matcher, so they count as one name.
        normalised = table["gpz_name"].map(normalise_name)
        duplicated = normalised.duplicated(keep=False)
        if duplicated.any():
            names = sorted(set(table.loc[duplicated, "gpz_name"]))
            raise ValueError(f"{self.id}: GPZ names listed twice: {names}")
        table["published_mw"] = table["published_mw"].astype("float64")
        return table


def _parse_mw(text: str, row: int) -> float:
    if text == "":
        return float("nan")  # not published
    try:
        value = float(text.replace(",", "."))
    except ValueError as exc:
        raise _row_error(row, f"published_mw {text!r} is not a number") from exc
    if not math.isfinite(value) or value < 0:
        raise _row_error(row, f"published_mw {value} must be a finite number ≥ 0")
    return value


class ManualPointsSource(ManualFileSource):
    """A PSE list of points (closed switchyards, ODB entries, planned stations) from a file."""

    id: ClassVar[str]
    # True for lists whose stations can be found by name (they exist in OSM): rows may lack coordinates.
    locate_by_name: ClassVar[bool] = False

    def native(self, path: Path) -> pd.DataFrame | None:
        """PSE's own published file turned into canonical rows, or None when `path` is not such a file."""
        return None

    def store_file(self, store: SnapshotStore) -> Snapshot:
        """Store PSE's own file as the reduced canonical CSV; any other file as it is."""
        table = self.native(self.path)
        if table is None:
            return super().store_file(store)
        content = _as_text(table).to_csv(index=False)
        return store.save(self.id, content.encode("utf-8"), suffix=".csv", url=f"manual:{self.path.name}")

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        native = self.native(snapshot.path)
        raw = read_table(snapshot.path) if native is None else _as_text(native)
        _require(raw, POINT_COLUMNS, f"{self.id} point file")
        has_xy = {"x_2180", "y_2180"} <= set(raw.columns)
        has_ll = {"lat", "lon"} <= set(raw.columns)
        if not (has_xy or has_ll or self.locate_by_name):
            raise ValueError(
                f"{self.id} point file: needs x_2180 + y_2180, or lat + lon; found {list(raw.columns)}"
            )
        names: list[str] = []
        dates: list[pd.Timestamp] = []
        geoms: list[Point | None] = []
        for i, rec in enumerate(raw.to_dict("records")):
            name = str(rec["name"])
            if not name:
                raise _row_error(i, "name is empty")
            names.append(name)
            dates.append(_parse_date(str(rec["as_of"]), i))
            if not (has_xy or has_ll):
                geoms.append(None)  # located by name later (model/capacity.locate_points)
                continue
            x, y = _coordinates(rec, has_xy, i)
            if not _inside_poland(x, y):
                raise _row_error(i, f"point ({x:.0f}, {y:.0f}) lies outside Poland; check the coordinates")
            geoms.append(Point(x, y))
        return gpd.GeoDataFrame({"name": names, "as_of": dates}, geometry=geoms, crs=f"EPSG:{EPSG}")


def _as_text(table: pd.DataFrame) -> pd.DataFrame:
    """A native table in the form `read_table` gives: every cell as text, dates as YYYY-MM-DD."""
    return table.assign(as_of=table["as_of"].dt.strftime("%Y-%m-%d")).astype(str)


def _coordinates(rec: Mapping[Any, Any], has_xy: bool, row: int) -> tuple[float, float]:
    if has_xy:
        x_text, y_text = str(rec["x_2180"]), str(rec["y_2180"])
        try:
            return float(x_text.replace(",", ".")), float(y_text.replace(",", "."))
        except ValueError as exc:
            raise _row_error(row, f"x_2180/y_2180 {x_text!r}/{y_text!r} are not numbers") from exc
    lat_text, lon_text = str(rec["lat"]), str(rec["lon"])
    try:
        lat, lon = float(lat_text.replace(",", ".")), float(lon_text.replace(",", "."))
    except ValueError as exc:
        raise _row_error(row, f"lat/lon {lat_text!r}/{lon_text!r} are not numbers") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise _row_error(row, f"lat/lon {lat}/{lon} out of range")
    x, y = _WGS84_TO_2180.transform(lon, lat)
    return float(x), float(y)


def _inside_poland(x: float, y: float) -> bool:
    x0, y0, x1, y1 = POLAND_BOUNDS
    return x0 <= x <= x1 and y0 <= y <= y1


class EneaCapacity(ManualCapacitySource):
    id = "enea"


class EnergaCapacity(ManualCapacitySource):
    id = "energa"


class TauronCapacity(ManualCapacitySource):
    id = "tauron"


class PseClosedPoints(ManualPointsSource):
    id = "pse_closed"
    locate_by_name = True

    def native(self, path: Path) -> pd.DataFrame | None:
        return read_pse_closed(path)


class PseOdbPoints(ManualPointsSource):
    id = "pse_odb"
    locate_by_name = True

    def native(self, path: Path) -> pd.DataFrame | None:
        return read_pse_odb(path)


class PseAvailPoints(ManualPointsSource):
    """`pse_avail` is the PSE availability report; the planned load stations in it feed the grid bonus."""

    id = "pse_avail"

    def native(self, path: Path) -> pd.DataFrame | None:
        return read_pse_avail(path)


MANUAL_CAPACITY: dict[str, type[ManualCapacitySource]] = {
    "enea": EneaCapacity,
    "energa": EnergaCapacity,
    "tauron": TauronCapacity,
}
MANUAL_POINTS: dict[str, type[ManualPointsSource]] = {
    "pse_closed": PseClosedPoints,
    "pse_odb": PseOdbPoints,
    "pse_avail": PseAvailPoints,
}


def load_gpz_mapping(path: Path) -> dict[str, dict[str, int]]:
    """`data/manual/gpz_mapping.csv` → {dso: {normalised DSO GPZ name: OSM substation id}}.

    Lines starting with `#` are comments. A DSO name listed twice is an error: the mapping would be
    ambiguous.
    """
    raw = read_table(path, comment="#")
    _require(raw, ("osm_id", "dso", "dso_gpz_name"), "gpz_mapping.csv")
    out: dict[str, dict[str, int]] = {}
    for i, rec in enumerate(raw.to_dict("records")):
        dso = str(rec["dso"]).lower()
        key = normalise_name(str(rec["dso_gpz_name"]))
        if not key or not str(rec["osm_id"]):
            continue  # blank template rows
        try:
            osm_id = parse_osm_ref(str(rec["osm_id"]))
        except ValueError as exc:
            raise _row_error(
                i, f"osm_id {rec['osm_id']!r} is not an OSM reference (use 123, n123 or w123)"
            ) from exc
        bucket = out.setdefault(dso, {})
        if key in bucket:
            raise _row_error(i, f"{dso} GPZ {rec['dso_gpz_name']!r} is mapped twice")
        bucket[key] = osm_id
    return out
