"""Energa-Operator: the statutory load-capacity PDF, per group of coherent 110 kV nodes (Phase 3).

Since the 30.04.2026 amendment Energa publishes "Informacja o wartości łącznej dostępnej odbiorczej mocy
przyłączeniowej" on its "Informacje o stanie przyłączeń" page (verified 19.10.2026, `docs/SOURCES.md`). Two
tables matter:

- Tabela 2.1: each group (number, name) and the 110 kV stations in it ("Model sieci na 2026r."). Stations
  that join a group later ("Mielno (2028-2035)") are left out: they do not exist yet.
- Tabela 2.3: the available load capacity of each group in MW, for the years 2026–2031 and 2035.

The capacity is shared by the whole group, so every station in a group gets the group's value for the first
year (the current state), with the group named in the note. That is a 110 kV-level signal, the one the brief
asked for, but still not a promise for one 60 MW site.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin

import pandas as pd
import pdfplumber

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.gpz_match import normalise_name
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

PAGE_URL = "https://energa-operator.pl/przylaczenie-do-sieci/informacje-o-stanie-przylaczen"
COLUMNS = ["dso", "gpz_name", "published_mw", "as_of", "note", "group_no", "group_name"]
_PDF_LINK = re.compile(r'href="([^"]*odbiorczej_mocy_przylaczeniowej[^"]*\.pdf)"', re.IGNORECASE)
_AS_OF = re.compile(r"STAN NA DZIEŃ\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE)
_YEAR = re.compile(r"20\d\d")


class EnergaConnector(Source):
    id = "energa"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        """Find the load-capacity PDF on the page and store it. The link redirects to a signed CDN URL."""
        page = client.get(PAGE_URL)
        page.raise_for_status()
        links = _PDF_LINK.findall(page.text)
        if not links:
            raise ValueError(
                "energa: no load-capacity PDF ('odbiorczej_mocy_przylaczeniowej') linked on the page"
            )
        url = urljoin(PAGE_URL, links[0])
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ValueError(f"energa: {url} did not return a PDF")
        return store.save(self.id, resp.content, suffix=".pdf", url=url)

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        with pdfplumber.open(snapshot.path) as pdf:
            first_text = pdf.pages[0].extract_text() or ""
            tables = [t for page in pdf.pages for t in page.extract_tables()]
        return capacity_table(first_text, tables)


def capacity_table(first_page_text: str, tables: list[list[list[Any]]]) -> pd.DataFrame:
    """Join the station lists (Tabela 2.1) with the group values (Tabela 2.3). Pure, for tests."""
    m = _AS_OF.search(first_page_text)
    if not m:
        raise ValueError("energa: no 'stan na dzień dd.mm.rrrr' on the first page")
    as_of = pd.Timestamp(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    groups = _station_groups(tables)
    values, year = _group_values(tables)
    if not groups or not values:
        raise ValueError(f"energa: found {len(groups)} station groups and {len(values)} group values")
    missing = sorted(set(groups) - set(values))
    if missing:
        raise ValueError(f"energa: groups {missing} have stations but no value in Tabela 2.3")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for no, (group_name, stations) in sorted(groups.items()):
        mw = values[no]
        for station in stations:
            key = normalise_name(station)
            if key in seen:  # a station listed in two groups: keep the first, as the matcher needs one name
                continue
            seen.add(key)
            rows.append(
                {
                    "dso": "energa",
                    "gpz_name": station,
                    "published_mw": mw,
                    "as_of": as_of,
                    "note": f"grupa {no} {group_name}: moc wspólna grupy węzłów 110 kV, rok {year}",
                    "group_no": no,
                    "group_name": group_name,
                }
            )
    table = pd.DataFrame(rows, columns=COLUMNS)
    table["published_mw"] = table["published_mw"].astype("float64")
    return table


def _cell(value: Any) -> str:
    return " ".join(str(value or "").split())


def _station_groups(tables: list[list[list[Any]]]) -> dict[int, tuple[str, list[str]]]:
    """Tabela 2.1: {group number: (group name, stations in the 2026 model)}.

    Cells wrap at word boundaries, so joining the lines with a space restores names like "Kalisz Pomorski".
    A row without a number continues the group above it (a group split across pages).
    """
    groups: dict[int, tuple[str, list[str]]] = {}
    last: int | None = None
    for table in tables:
        if not table or len(table[0]) != 4 or not _cell(table[0][2]).startswith("Model sieci"):
            continue
        for row in table[1:]:
            no_text, name, stations_text = _cell(row[0]), _cell(row[1]), _cell(row[2])
            if no_text.isdigit():
                last = int(no_text)
                groups[last] = (name, [])
            elif no_text or name or not stations_text or last is None:
                continue  # a branch heading ("ENERGA-OPERATOR S.A. Oddział Olsztyn") or an empty row
            stations = groups[last][1]
            stations.extend(s.strip() for s in stations_text.split(",") if s.strip())
    return groups


def _group_values(tables: list[list[list[Any]]]) -> tuple[dict[int, float], int]:
    """Tabela 2.3: {group number: MW in the first year listed}, and that year."""
    values: dict[int, float] = {}
    year: int | None = None
    for table in tables:
        if not table or _cell(table[0][0]) != "Nr grupy" or _cell(table[0][2]) != "Rok":
            continue
        years = [int(c) for c in (_cell(c) for c in table[1]) if _YEAR.fullmatch(c)]
        if not years:
            raise ValueError("energa: Tabela 2.3 has no year row")
        year = years[0]
        for row in table[2:]:
            no_text, first = _cell(row[0]), _cell(row[2]).replace(",", ".")
            if not no_text.isdigit():
                continue
            try:
                values[int(no_text)] = float(first)
            except ValueError as exc:
                raise ValueError(f"energa: group {no_text} has value {first!r} for {year}") from exc
    if year is None:
        return {}, 0
    return values, year
