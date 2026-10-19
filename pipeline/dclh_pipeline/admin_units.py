"""Load PRG administrative units into PostGIS and count grid stations per voivodeship.

`admin_units` holds voivodeships, powiats and gminas in EPSG:2180, with the parent TERYT taken from the code
prefix (powiat `1421` -> voivodeship `14`, gmina `1421011` -> powiat `1421`). The country polygon is not
stored here; the AOI table has what the app needs from it.

`substation_counts` is the Phase 1 plausibility check: loaded stations per voivodeship and class, plus the
stations that fall outside Poland (the Geofabrik extract reaches a little over the border).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
from shapely import wkb
from sqlalchemy import text
from sqlalchemy.orm import Session

LEVELS = ("voivodeship", "powiat", "gmina")
# TERYT code length per level, and the length of the parent's prefix.
CODE_LENGTH = {"voivodeship": 2, "powiat": 4, "gmina": 7}
PARENT_LENGTH = {"voivodeship": None, "powiat": 2, "gmina": 4}
OUTSIDE_POLAND = "(outside Poland)"

_INSERT = text(
    """
    INSERT INTO admin_units (teryt, level, name, parent_teryt, geom)
    VALUES (:teryt, :level, :name, :parent_teryt, ST_Multi(ST_SetSRID(ST_GeomFromWKB(:wkb), 2180)))
    """
)

# One voivodeship per station: a point on a shared border would otherwise be counted twice.
_COUNTS = text(
    """
    SELECT v.teryt, v.name, s.cls, s.dso, count(*)
    FROM substations s
    LEFT JOIN LATERAL (
        SELECT a.teryt, a.name FROM admin_units a
        WHERE a.level = 'voivodeship' AND ST_Covers(a.geom, s.geom)
        ORDER BY a.teryt LIMIT 1
    ) v ON true
    GROUP BY v.teryt, v.name, s.cls, s.dso
    """
)


def _row(record: Any) -> dict[str, Any]:
    level, teryt = str(record.level), str(record.teryt)
    if len(teryt) != CODE_LENGTH[level]:
        raise ValueError(f"PRG {level} {record.name!r}: TERYT {teryt!r} is not {CODE_LENGTH[level]} digits")
    parent_len = PARENT_LENGTH[level]
    return {
        "teryt": teryt,
        "level": level,
        "name": str(record.name),
        "parent_teryt": teryt[:parent_len] if parent_len else None,
        "wkb": wkb.dumps(record.geometry),
    }


def load_admin_units(session: Session, prg: gpd.GeoDataFrame) -> Counter[str]:
    """Replace `admin_units` with the voivodeships, powiats and gminas of the parsed PRG table.

    Runs inside the caller's transaction, so a failure part-way keeps the previous rows once it rolls back.
    Returns the rows written per level.
    """
    if prg.crs is None or prg.crs.to_epsg() != 2180:
        raise ValueError(f"PRG table must be in EPSG:2180, got {prg.crs}")
    units = prg[prg["level"].isin(LEVELS)]
    rows = [_row(r) for r in units.itertuples()]
    session.execute(text("DELETE FROM admin_units"))
    if rows:
        session.execute(_INSERT, rows)
    return Counter(r["level"] for r in rows)


@dataclass(frozen=True)
class VoivodeshipCount:
    teryt: str | None  # None for stations outside Poland
    name: str
    by_class: Counter[str]
    by_dso: Counter[str]  # GPZ_110_SN only; "-" when the operator tag names no DSO


def substation_counts(session: Session) -> list[VoivodeshipCount]:
    """Loaded substations per voivodeship, by class and (for GPZs) by DSO. Needs `admin_units` loaded."""
    voivodeships = text("SELECT count(*) FROM admin_units WHERE level = 'voivodeship'")
    if session.execute(voivodeships).scalar_one() == 0:
        raise ValueError("admin_units has no voivodeships; run `dclh load-admin` first")
    found: dict[str | None, VoivodeshipCount] = {}
    for teryt, name, cls, dso, n in session.execute(_COUNTS):
        entry = found.setdefault(teryt, VoivodeshipCount(teryt, name or OUTSIDE_POLAND, Counter(), Counter()))
        entry.by_class[cls] += n
        if cls == "GPZ_110_SN":
            entry.by_dso[dso or "-"] += n
    # voivodeships by TERYT, the stations outside Poland last
    return sorted(found.values(), key=lambda c: (c.teryt is None, c.teryt or ""))
