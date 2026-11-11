"""The ranked site list for Claude: filters the web list lacks (verdict, one site per gmina or region),
the reasons behind each verdict, the nearest GPZ and how complete the data is, so "top 5" needs no follow-up.

Everything here is read-only. Grid values stay what they are in the database: estimates with a source, a date
and a confidence. `project_mw` is the target project; `estimated_mw` is land capacity, not grid power.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from mcp.server.fastmcp.exceptions import ToolError
from shapely import STRtree, wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.mcp.context import site_url
from dclh_api.routers.sites import (
    _PLACE_COLUMNS,
    _PLACE_JOINS,
    _PLACE_SOURCE,
    _SUMMARY_COLUMNS,
    SORT_COLUMNS,
    project_capacity_of,
)

Profile = Literal["built", "container"]
Verdict = Literal["green", "yellow", "red", "manual"]
PROFILES: tuple[Profile, ...] = ("built", "container")
VERDICTS: tuple[Verdict, ...] = ("green", "yellow", "red", "manual")

SHORT_TO_CHECK = 3  # what the short list shows of "still to check"; `detail="full"` shows all
SHORT_PARCELS = 5
SIMPLIFY_M = 25  # candidate outlines are thinned for the distance check; 25 m is nothing next to kilometres
CURSOR_PREFIX = "o"


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"{CURSOR_PREFIX}{offset}".encode()).decode()


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ToolError("Kursor jest nieprawidłowy. Zacznij od pierwszej strony.") from exc
    if not raw.startswith(CURSOR_PREFIX) or not raw[1:].isdigit():
        raise ToolError("Kursor jest nieprawidłowy. Zacznij od pierwszej strony.")
    return int(raw[1:])


def _filters(
    *, min_mw: float, min_ha: float, powiat: str | None, voivodeship: str | None
) -> tuple[str, dict[str, Any]]:
    where = "WHERE kind = 'standard' AND estimated_mw >= :min_mw AND area_ha >= :min_ha AND NOT excluded"
    params: dict[str, Any] = {"min_mw": min_mw, "min_ha": min_ha}
    if powiat is not None:
        where += " AND powiat = :powiat"
        params["powiat"] = powiat
    if voivodeship is not None:
        where += " AND left(powiat, 2) = :voivodeship"
        params["voivodeship"] = voivodeship
    return where, params


def _ranked_ctes(
    *, where: str, column: str, max_per_gmina: int | None, max_per_voivodeship: int | None
) -> str:
    """CTEs ending in `kept(site_id, sv)`: the filtered sites, thinned to the per-gmina and per-region caps.

    `sv` is the sort value, best first. A site outside every gmina (no admin unit holds its point) is its own
    group, so the cap never hides it.
    """
    point = ", ST_PointOnSurface(geom) AS pt" if max_per_gmina is not None else ""
    parts = [f"cand AS (SELECT site_id, {column} AS sv, powiat{point} FROM sites {where})"]
    source = "cand"
    if max_per_gmina is not None:
        parts.append(
            "by_gmina AS (SELECT c.site_id, c.sv, c.powiat, row_number() OVER ("
            "PARTITION BY coalesce(a.teryt, c.site_id) ORDER BY c.sv DESC, c.site_id) AS rn "
            "FROM cand c LEFT JOIN admin_units a ON a.level = 'gmina' AND ST_Covers(a.geom, c.pt)), "
            "capped_gmina AS (SELECT site_id, sv, powiat FROM by_gmina WHERE rn <= :max_per_gmina)"
        )
        source = "capped_gmina"
    if max_per_voivodeship is not None:
        parts.append(
            "by_region AS (SELECT site_id, sv, powiat, row_number() OVER ("
            f"PARTITION BY left(powiat, 2) ORDER BY sv DESC, site_id) AS rn FROM {source}), "
            "capped_region AS (SELECT site_id, sv, powiat FROM by_region WHERE rn <= :max_per_voivodeship)"
        )
        source = "capped_region"
    parts.append(f"kept AS (SELECT site_id, sv FROM {source})")
    return "WITH " + ", ".join(parts)


# Rows of `page(site_id, sv)` with everything the list lines and the comparison need. Detail JSON is read only
# here, for the page, never for the whole filtered set.
_ROWS_SQL = f"""
SELECT p.*, {_PLACE_COLUMNS}, gpz.name AS gpz_name FROM (
    SELECT {_SUMMARY_COLUMNS}, verdict_built, verdict_container, {_PLACE_SOURCE}, geom AS site_geom,
           parcel_ids, profiles, detail -> 'features' AS features,
           detail -> 'score' -> 'categories' AS categories, pg.sv
    FROM sites JOIN page pg USING (site_id)
) p {_PLACE_JOINS}
LEFT JOIN LATERAL (
    SELECT name FROM substations WHERE cls = 'GPZ_110_SN' ORDER BY geom <-> p.site_geom LIMIT 1
) gpz ON true
ORDER BY p.sv DESC, p.site_id
"""


def _rows(session: Session, ctes: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in session.execute(text(f"{ctes} {_ROWS_SQL}"), params).mappings()]
    except SQLAlchemyError as exc:
        raise ToolError("Baza danych jest niedostępna.") from exc


def _fact_value(features: dict[str, Any], key: str) -> Any:
    fact = (features or {}).get(key)
    return None if fact is None else fact.get("value")


def _completeness(features: dict[str, Any]) -> dict[str, int]:
    """Facts by confidence. `missing` means the data is absent (not that the site is bad)."""
    counts = {"verified": 0, "estimated": 0, "missing": 0}
    for fact in (features or {}).values():
        confidence = "missing" if fact is None else fact.get("confidence", "missing")
        counts[confidence if confidence in counts else "missing"] += 1
    return counts


def _nearest_gpz(row: dict[str, Any]) -> dict[str, Any]:
    fact = (row["features"] or {}).get("dist_gpz110_km")
    distance = None if fact is None else round(float(fact["value"]), 2)
    return {
        "name": row["gpz_name"],
        "distance_km": distance,
        "confidence": "missing" if fact is None else fact["confidence"],
        "source_id": None if fact is None else fact["source_id"],
        "as_of": None if fact is None or fact["as_of"] is None else fact["as_of"],
    }


def _parcels(parcel_ids: list[str], *, full: bool) -> dict[str, Any]:
    """Parcel ids look like `046401_1.0008.16` (unit.obręb.number), the form `check_parcel` takes."""
    obreb = sorted({pid.rsplit(".", 1)[0] for pid in parcel_ids})
    shown = parcel_ids if full else parcel_ids[:SHORT_PARCELS]
    return {"count": len(parcel_ids), "obreb": obreb, "ids": shown}


def _why(row: dict[str, Any], profile: str, *, full: bool) -> dict[str, Any]:
    verdict = ((row["profiles"] or {}).get(profile)) or {}
    to_check = [item["text_pl"] for item in verdict.get("to_check", [])]
    why: dict[str, Any] = {
        "blockers": [b["text_pl"] for b in verdict.get("blockers", [])],
        "to_check": to_check if full else to_check[:SHORT_TO_CHECK],
        "to_check_total": len(to_check),
    }
    if full:
        why["positives"] = [p["text_pl"] for p in verdict.get("positives", [])]
    return why


def site_line(
    row: dict[str, Any], public_url: str, *, profile: Profile | None = None, full: bool = False
) -> dict[str, Any]:
    built, container = row["verdict_built"], row["verdict_container"]
    in_scope = (profile,) if profile else PROFILES
    project_mw, spare_ha = project_capacity_of(row)
    return {
        "site_id": row["site_id"],
        "url": site_url(public_url, row["site_id"]),
        "gmina": row["gmina"],
        "powiat": row["powiat"],
        "voivodeship": row["voivodeship"],
        "area_ha": round(row["area_ha"], 2),
        "project_mw": round(project_mw, 1),
        "spare_ha": round(spare_ha, 2),
        "estimated_mw": round(row["estimated_mw"], 1),
        "score": round(row["score"], 1),
        "verdicts": {"built": built, "container": container} if built and container else None,
        "grid_badge": row["grid_badge"],
        "flags": row["flags"],
        "why": {p: _why(row, p, full=full) for p in in_scope},
        "nearest_gpz": _nearest_gpz(row),
        "parcels": _parcels(row["parcel_ids"], full=full),
        "data": _completeness(row["features"]),
    }


def verdict_counts(
    session: Session, where: str, params: dict[str, Any], profile: Profile | None
) -> dict[str, Any]:
    """Sites per verdict under the same filters (ignoring the verdict filter), per profile in scope."""
    counts: dict[str, dict[str, int]] = {}
    try:
        for p in (profile,) if profile else PROFILES:
            rows = session.execute(
                text(f"SELECT verdict_{p} AS v, count(*) AS n FROM sites {where} GROUP BY 1"), params
            ).mappings()
            found = {r["v"]: r["n"] for r in rows}
            counts[p] = {v: int(found.get(v, 0)) for v in VERDICTS}
    except SQLAlchemyError as exc:
        raise ToolError("Baza danych jest niedostępna.") from exc
    return counts


def spread(
    candidates: list[tuple[str, str, BaseGeometry]], distance_m: float, per_region: int | None
) -> list[str]:
    """Site ids, best first, with no two closer than `distance_m` (outline to outline).

    `candidates` is (site_id, region, outline) in rank order. A site is skipped when a better pick is within
    reach, or when its region already has `per_region` picks. A skipped site hides nothing else.
    """
    if not candidates:
        return []
    tree = STRtree([outline for _, _, outline in candidates])
    hidden = [False] * len(candidates)
    per: dict[str, int] = {}
    picked: list[str] = []
    for i, (site_id, region, outline) in enumerate(candidates):
        if hidden[i] or (per_region is not None and per.get(region, 0) >= per_region):
            continue
        picked.append(site_id)
        per[region] = per.get(region, 0) + 1
        for j in tree.query(outline, predicate="dwithin", distance=distance_m):
            hidden[int(j)] = True
    return picked


def _spread_ids(
    session: Session, ctes: str, params: dict[str, Any], distance_km: float, per_region: int | None
) -> list[str]:
    try:
        rows = session.execute(
            text(
                f"{ctes} SELECT k.site_id, left(s.powiat, 2) AS region, "
                f"ST_AsBinary(ST_SimplifyPreserveTopology(s.geom, {SIMPLIFY_M})) AS outline "
                "FROM kept k JOIN sites s USING (site_id) ORDER BY k.sv DESC, k.site_id"
            ),
            params,
        ).mappings()
        candidates = [(r["site_id"], r["region"], wkb.loads(bytes(r["outline"]))) for r in rows]
    except SQLAlchemyError as exc:
        raise ToolError("Baza danych jest niedostępna.") from exc
    return spread(candidates, distance_km * 1000, per_region)


def search_sites(
    session: Session,
    public_url: str,
    *,
    powiat: str | None,
    voivodeship: str | None,
    min_mw: float,
    min_ha: float,
    profile: Profile | None,
    hide_red: bool,
    verdicts: list[Verdict] | None,
    sort: Literal["score", "upside", "area", "mw"],
    limit: int,
    cursor: str | None,
    max_per_gmina: int | None = None,
    max_per_voivodeship: int | None = None,
    min_distance_km: float | None = None,
    full: bool = False,
) -> dict[str, Any]:
    if (hide_red or verdicts) and profile is None:
        raise ToolError("Filtr werdyktu i ukrywanie odrzuconych wymagają wybranego profilu (profile).")
    base_where, params = _filters(min_mw=min_mw, min_ha=min_ha, powiat=powiat, voivodeship=voivodeship)
    where = base_where
    if hide_red:
        where += f" AND verdict_{profile} IS DISTINCT FROM 'red'"  # `profile` is a Literal: never user text
    if verdicts:
        where += f" AND verdict_{profile} = ANY(:verdicts)"
        params["verdicts"] = list(verdicts)
    caps: dict[str, Any] = {}
    if max_per_gmina is not None:
        caps["max_per_gmina"] = max_per_gmina
    if max_per_voivodeship is not None:
        caps["max_per_voivodeship"] = max_per_voivodeship
    if min_distance_km is not None:
        caps["min_distance_km"] = min_distance_km
    # With a distance the region cap is applied together with it (below), not before it: a site that a pick in
    # another region hides must not use up that region's place.
    ctes = _ranked_ctes(
        where=where,
        column=SORT_COLUMNS[sort],
        max_per_gmina=max_per_gmina,
        max_per_voivodeship=None if min_distance_km is not None else max_per_voivodeship,
    )
    sql_caps = {k: v for k, v in caps.items() if k in ("max_per_gmina", "max_per_voivodeship")}
    offset = decode_cursor(cursor) if cursor else 0
    if min_distance_km is not None:
        picked = _spread_ids(session, ctes, params | sql_caps, min_distance_km, max_per_voivodeship)
        total = len(picked)
        page_ids = picked[offset : offset + limit]
        page_ctes = f"{ctes}, page AS (SELECT site_id, sv FROM kept WHERE site_id = ANY(:page_ids))"
        rows = _rows(session, page_ctes, params | sql_caps | {"page_ids": page_ids}) if page_ids else []
    else:
        try:
            total = session.execute(text(f"{ctes} SELECT count(*) FROM kept"), params | sql_caps).scalar_one()
        except SQLAlchemyError as exc:
            raise ToolError("Baza danych jest niedostępna.") from exc
        page_ctes = (
            f"{ctes}, page AS (SELECT site_id, sv FROM kept "
            "ORDER BY sv DESC, site_id LIMIT :limit OFFSET :offset)"
        )
        rows = _rows(session, page_ctes, params | sql_caps | {"limit": limit, "offset": offset})
    result: dict[str, Any] = {
        "total": total,
        "next_cursor": encode_cursor(offset + len(rows)) if offset + len(rows) < total else None,
        "verdict_counts": verdict_counts(session, base_where, params, profile),
        "sites": [site_line(r, public_url, profile=profile, full=full) for r in rows],
    }
    if caps:
        result["caps"] = caps
    return result


def compare_lines(
    session: Session, public_url: str, site_ids: list[str], *, profile: Profile | None, full: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    """Lines for the given sites in the order asked, and the ids that do not exist."""
    ctes = "WITH page AS (SELECT site_id, 0 AS sv FROM sites WHERE site_id = ANY(:ids))"
    rows = {r["site_id"]: r for r in _rows(session, ctes, {"ids": site_ids})}
    lines = [
        site_line(rows[i], public_url, profile=profile, full=full) | _compare_extras(rows[i])
        for i in site_ids
        if i in rows
    ]
    return lines, [i for i in site_ids if i not in rows]


_GRID_KEYS = (
    "dist_line110_km",
    "dist_gpz110_km",
    "dist_node_220_400_km",
    "independent_110_lines_near",
    "published_gpz_headroom_mw",
)


def _compare_extras(row: dict[str, Any]) -> dict[str, Any]:
    """What `compare_sites` adds to a line: the score by category and the grid facts behind it."""
    features = row["features"] or {}
    grid = {}
    for key in _GRID_KEYS:
        fact = features.get(key)
        grid[key] = (
            None if fact is None else {k: fact[k] for k in ("value", "source_id", "as_of", "confidence")}
        )
    return {
        "score_parts": [
            {"category": c["category"], "points": c["points"], "max_points": c["max_points"]}
            for c in row["categories"] or []
        ],
        "grid": grid,
    }
