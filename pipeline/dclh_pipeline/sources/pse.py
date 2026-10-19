"""Readers for the two PSE lists as PSE publishes them (XLSX), verified 19.10.2026 (`docs/SOURCES.md`).

- `pse_closed`: "Lista rozdzielni PSE S.A., dla których brak jest dostępnych miejsc przyłączenia". A title
  block, a "Stan na dzień d.mm.rrrr" cell, then the columns `ZKO PSE`, `Symbol stacji`, `Nazwa stacji`,
  `Rozdzielnia`, `Liczba dostępnych miejsc przyłączenia`. Only the first table is read: the tables below it
  list planned switchyards by their position in the development plan ("III.33"), which have no name or
  place to find.
- `pse_odb`: "Informacje publikowane zgodnie z Art. 7 ust. 8l ustawy PE" (monthly). Only rows whose
  installation type is `ODB` (loads, mostly data centres) are kept.

Neither list has coordinates; the stations are located later by name (`model/capacity.locate_points`).

**Personal data (brief 9.6).** The art. 7 ust. 8l list names the applicant ("Nazwa podmiotu ubiegającego się
o przyłączenie"), which can be a person, and the object name can carry it too. Only the station, voltage,
type, MW, status and dates are read. The other columns never leave this module, and the manual import
stores the reduced table, not PSE's file.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_HEADER_SCAN_ROWS = 15


def _clean(value: Any) -> str:
    return "" if value is None or pd.isna(value) else " ".join(str(value).split())


def _sheet(path: Path) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=0, header=None, dtype=object)


def _as_of(sheet: pd.DataFrame, header_row: int) -> pd.Timestamp:
    for row in range(header_row):
        for cell in sheet.iloc[row]:
            text = _clean(cell)
            if "stan na" in text.lower() and (m := _DATE.search(text)):
                return pd.Timestamp(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    raise ValueError("PSE list: no 'Stan na dd.mm.rrrr' above the header")


def _header_row(sheet: pd.DataFrame, first_column: str) -> int | None:
    for row in range(min(_HEADER_SCAN_ROWS, len(sheet))):
        if _clean(sheet.iat[row, 0]).lower().startswith(first_column.lower()):
            return row
    return None


def read_pse_closed(path: Path) -> pd.DataFrame | None:
    """The closed-switchyard list as `name, as_of, symbol, switchyard`; None if the file is not that list."""
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    sheet = _sheet(path)
    header = _header_row(sheet, "ZKO PSE")
    if header is None:
        return None
    columns = [_clean(c) for c in sheet.iloc[header]]
    try:
        symbol, name, switchyard = (
            columns.index(c) for c in ("Symbol stacji", "Nazwa stacji", "Rozdzielnia")
        )
    except ValueError as exc:
        raise ValueError(f"PSE closed list: unexpected columns {columns}") from exc
    as_of = _as_of(sheet, header)
    rows: list[dict[str, Any]] = []
    for _, rec in sheet.iloc[header + 1 :].iterrows():
        station, code = _clean(rec.iloc[name]), _clean(rec.iloc[symbol])
        if not any(_clean(c) for c in rec):
            break  # the existing stations end here; below come planned switchyards known only by plan number
        if not station or not code:
            continue
        rows.append(
            {"name": station, "as_of": as_of, "symbol": code, "switchyard": _clean(rec.iloc[switchyard])}
        )
    if not rows:
        raise ValueError("PSE closed list: no stations under the header")
    return pd.DataFrame(rows, columns=["name", "as_of", "symbol", "switchyard"])


# Columns of the art. 7 ust. 8l list that may be read: start of the header text → our column name.
# The applicant ("Nazwa podmiotu ...") and object name ("Nazwa Obiektu") are deliberately not here.
_ODB_COLUMNS = {
    "Lokalizacja miejsca przyłączenia": "name",
    "Poziom napięcia": "voltage_kv",
    "Rodzaj instalacji": "kind",
    "ODB [MW]": "odb_mw",
    "STATUS ETAPU PROJEKTU": "status",
    "Data złożenia wniosku": "applied_on",
    "Data określenia warunków": "conditions_on",
    "Data zawarcia umowy": "agreement_on",
}


def read_pse_odb(path: Path) -> pd.DataFrame | None:
    """ODB rows of the art. 7 ust. 8l list, one row per station named in the location cell.

    Returns `name, as_of, voltage_kv, odb_mw, status, applied_on, conditions_on, agreement_on`, or None when
    the file is not that list.
    """
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return None
    sheet = _sheet(path)
    header = _header_row(sheet, "ID Obiektu")
    if header is None:
        return None
    columns = [_clean(c) for c in sheet.iloc[header]]
    index: dict[str, int] = {}
    for prefix, ours in _ODB_COLUMNS.items():
        found = [i for i, c in enumerate(columns) if c.startswith(prefix)]
        if len(found) != 1:
            raise ValueError(f"PSE art. 7 ust. 8l list: column {prefix!r} found {len(found)} times")
        index[ours] = found[0]
    as_of = _as_of(sheet, header)
    rows: list[dict[str, Any]] = []
    for _, rec in sheet.iloc[header + 1 :].iterrows():
        if _clean(rec.iloc[index["kind"]]) != "ODB":
            continue
        values = {ours: _clean(rec.iloc[i]) for ours, i in index.items() if ours not in ("name", "kind")}
        values = {k: _date_text(v) if k.endswith("_on") else v for k, v in values.items()}
        # "Kromolice\nKromolice" or "A; B": several connection points; each station counts once.
        stations = dict.fromkeys(
            s.strip() for s in re.split(r"[\n;]", str(rec.iloc[index["name"]])) if s.strip()
        )
        rows.extend({"name": s, "as_of": as_of, **values} for s in stations if s != "nan")
    if not rows:
        raise ValueError("PSE art. 7 ust. 8l list: no ODB rows")
    return pd.DataFrame(
        rows, columns=["name", "as_of", *(c for c in _ODB_COLUMNS.values() if c not in ("name", "kind"))]
    )


def _date_text(value: str) -> str:
    """Excel dates arrive as "2026-08-13 00:00:00"; keep the date. "-" (no date) becomes blank."""
    if value in ("", "-"):
        return ""
    return value[:10] if re.match(r"\d{4}-\d{2}-\d{2}", value) else value
