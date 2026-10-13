"""Load the parsed OSM grid into PostGIS, so Martin can serve it to the map.

Only what the map needs is stored: 110 kV + SN stations (GPZ), 220/400 kV nodes, stations whose voltage tag
is unreadable (kept at low confidence, never dropped) and lines of 110 kV and above. The roughly 30,000
medium-voltage-only stations stay out; they would bury the stations that matter.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import Any

import geopandas as gpd
from shapely import wkb
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.model.features import Line, Substation
from dclh_pipeline.model.grid import V_110, SubstationClass, derive_operator, is_foreign_operator
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.sources.osm_power import to_grid

SOURCE_ID = "osm_power"
LOADED_CLASSES = frozenset(
    {SubstationClass.GPZ_110_SN, SubstationClass.NODE_220_400, SubstationClass.UNKNOWN_VOLTAGE}
)
CHUNK = 5000

_INSERT_SUBSTATION = text(
    """
    INSERT INTO substations (osm_id, name, operator, dso, foreign_operator, voltages, cls, confidence,
                             source_id, as_of, geom)
    VALUES (:osm_id, :name, :operator, :dso, :foreign_operator, :voltages, :cls, :confidence,
            :source_id, :as_of, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180))
    """
)
_INSERT_LINE = text(
    """
    INSERT INTO lines (osm_id, voltage, operator, confidence, source_id, as_of, geom)
    VALUES (:osm_id, :voltage, NULL, :confidence, :source_id, :as_of, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180))
    """
)


@dataclass(frozen=True)
class GridLoadReport:
    substations: Counter[str]  # rows written, by class
    lines: int
    skipped_substations: int  # medium-voltage-only and 110 kV-only stations, not loaded
    skipped_lines: int  # below 110 kV, or without a readable voltage


def _chunks(rows: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    it = iter(rows)
    while chunk := list(islice(it, CHUNK)):
        yield chunk


def _substation_row(s: Substation, as_of: datetime | None) -> dict[str, Any]:
    dso = derive_operator(s.operator)
    return {
        "osm_id": s.osm_id,
        "name": s.name,
        "operator": s.operator,
        "dso": dso.value if dso else None,
        "foreign_operator": is_foreign_operator(s.operator),
        "voltages": list(s.voltages),
        "cls": s.cls.value,
        "confidence": (
            Confidence.MISSING if s.cls is SubstationClass.UNKNOWN_VOLTAGE else Confidence.ESTIMATED
        ).value,
        "source_id": SOURCE_ID,
        "as_of": as_of,
        "wkb": wkb.dumps(s.geom),
    }


def _line_row(line: Line, as_of: datetime | None) -> dict[str, Any]:
    return {
        "osm_id": line.osm_id,
        "voltage": line.voltage,
        "confidence": Confidence.ESTIMATED.value,
        "source_id": SOURCE_ID,
        "as_of": as_of,
        "wkb": wkb.dumps(line.geom),
    }


def load_grid(session: Session, table: gpd.GeoDataFrame, *, as_of: datetime | None) -> GridLoadReport:
    """Replace `substations` and `lines` with the grid in `table`. Runs inside the caller's transaction.

    A failure part-way leaves the previous rows in place once the caller rolls back.
    """
    substations, lines = to_grid(table)
    kept_subs = [s for s in substations if s.cls in LOADED_CLASSES]
    kept_lines = [
        ln
        for ln in lines
        if ln.voltage is not None and ln.voltage >= V_110 and ln.geom.geom_type == "LineString"
    ]
    session.execute(text("DELETE FROM substations"))
    session.execute(text("DELETE FROM lines"))
    for chunk in _chunks(_substation_row(s, as_of) for s in kept_subs):
        session.execute(_INSERT_SUBSTATION, chunk)
    for chunk in _chunks(_line_row(ln, as_of) for ln in kept_lines):
        session.execute(_INSERT_LINE, chunk)
    return GridLoadReport(
        substations=Counter(s.cls.value for s in kept_subs),
        lines=len(kept_lines),
        skipped_substations=len(substations) - len(kept_subs),
        skipped_lines=len(lines) - len(kept_lines),
    )
