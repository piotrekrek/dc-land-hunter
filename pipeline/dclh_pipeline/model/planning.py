"""Planning path of a site after the plan ogólny reform: OUZ, the legal route and the zone parameters.

Outside an obszar uzupełnienia zabudowy (OUZ), a gmina with an adopted plan ogólny cannot issue a WZ
decision, so the only route is an MPZP or a ZPI (12–24 months). Inside an OUZ a WZ is possible. All the
facts come from the Rejestr Urbanistyczny (`sources/ru.py`, verified live for gm. Rzepin 11.11.2026).

A gmina with no plan ogólny in the register gives no fact at all: that is "do sprawdzenia", never "bad" (the
register is incomplete until 30.11.2026).

Facts are scalars so they fit `FactOut`: `in_ouz` is yes | no | partial; `planning_path` is one of the
`PATH_*` values; the zone parameters are one fact each.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import geopandas as gpd
from shapely import STRtree, unary_union
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring.types import (
    IN_OUZ_NO,
    IN_OUZ_PARTIAL,
    IN_OUZ_YES,
    PATH_MPZP_IN_FORCE,
    PATH_MPZP_OR_ZPI,
    PATH_WZ_POSSIBLE,
    Fact,
)

SOURCE_ID = "ru"
# Share of the site area inside the OUZ above which the site counts as inside, and below which as outside.
OUZ_INSIDE_SHARE = 0.99
OUZ_OUTSIDE_SHARE = 0.01
MIN_OVERLAP_SHARE = 0.01  # as in features.MIN_ZONING_OVERLAP_SHARE

_IN_FORCE = "legalForce"
# The resolution id in the act's xlink ends like `POG_Rzepin_XXXIV_224_2026_uchwalajaca`.
_RESOLUTION = re.compile(r"_([IVXLCDM]+)_(\d+)_(\d{4})(?:_|$)")

# Output column of the zone table → fact key.
_PARAM_FACTS = {
    "max_height_m": "zone_max_height_m",
    "max_build_up_pct": "zone_max_build_up_pct",
    "min_pbc_pct": "zone_min_pbc_pct",
    "max_intensity": "zone_max_intensity",
}


@dataclass(frozen=True)
class PlanAct:
    """One plan ogólny in force: its extent, resolution number and the date it takes effect."""

    extent: BaseGeometry
    number: str | None  # "XXXIV/224/2026"; None when the resolution id does not follow the pattern
    title: str
    in_force_from: str | None  # ISO date


@dataclass(frozen=True)
class PlanZone:
    geom: BaseGeometry
    params: dict[str, float]  # keys of `_PARAM_FACTS`; a parameter the zone does not set is absent


@dataclass(frozen=True)
class PlanOgolnyLayer:
    """What a search area knows about plans ogólne: acts, zones with parameters, OUZ and MPZPs in force."""

    as_of: datetime | None
    acts: tuple[PlanAct, ...]
    zones: tuple[PlanZone, ...]
    ouz: tuple[BaseGeometry, ...]
    mpzp_in_force: tuple[BaseGeometry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_zone_tree", STRtree([z.geom for z in self.zones]))
        object.__setattr__(self, "_act_tree", STRtree([a.extent for a in self.acts]))


def resolution_number(href: str | None) -> str | None:
    """'…/POG_Rzepin_XXXIV_224_2026_uchwalajaca' → 'XXXIV/224/2026'."""
    m = _RESOLUTION.search((href or "").rstrip("/").rsplit("/", 1)[-1])
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else None


def _in_force(status_href: str | None) -> bool:
    return (status_href or "").rstrip("/").rsplit("/", 1)[-1] == _IN_FORCE


def _text(raw: Any) -> str | None:
    text = str(raw).strip()
    return None if text in ("", "nan", "None", "NaT") else text


def _number(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def build_plan_layer(
    zones: gpd.GeoDataFrame,
    ouz: gpd.GeoDataFrame,
    acts: gpd.GeoDataFrame,
    mpzp: gpd.GeoDataFrame,
    as_of: datetime | None,
) -> PlanOgolnyLayer:
    """The layer from the four parsed RU tables (zones, OUZ, plan acts, MPZP outlines). Only features in
    force count: a draft binds nobody."""
    plan_acts = tuple(
        PlanAct(
            geom,
            resolution_number(row.resolution_href),
            _text(row.title) or "plan ogólny",
            _text(row.in_force_from),
        )
        for row, geom in zip(acts.itertuples(), acts.geometry, strict=True)
        if _in_force(row.status_href)
    )
    plan_zones = []
    for row, geom in zip(zones.itertuples(), zones.geometry, strict=True):
        if not _in_force(row.status_href):
            continue
        params = {k: v for k in _PARAM_FACTS if (v := _number(getattr(row, k))) is not None}
        plan_zones.append(PlanZone(geom, params))
    return PlanOgolnyLayer(
        as_of,
        plan_acts,
        tuple(plan_zones),
        tuple(g for s, g in zip(ouz["status_href"], ouz.geometry, strict=True) if _in_force(s)),
        tuple(g for s, g in zip(mpzp["status_href"], mpzp.geometry, strict=True) if _in_force(s)),
    )


def _share(site: BaseGeometry, geoms: Sequence[BaseGeometry]) -> float:
    """Share (0..1) of the site's area covered by the union of `geoms`."""
    hits = [g for g in geoms if g.intersects(site)]
    if not hits or site.area <= 0:
        return 0.0
    return float(min(1.0, unary_union(hits).intersection(site).area / site.area))


def _dominant(site: BaseGeometry, tree: STRtree, geoms: Sequence[BaseGeometry]) -> int | None:
    """Index of the geometry covering most of the site, or None when none covers `MIN_OVERLAP_SHARE`."""
    best: tuple[float, int] | None = None
    for i in tree.query(site):
        overlap = geoms[int(i)].intersection(site).area
        if overlap >= site.area * MIN_OVERLAP_SHARE and (best is None or overlap > best[0]):
            best = (overlap, int(i))
    return None if best is None else best[1]


def planning_facts(
    site: BaseGeometry, layer: PlanOgolnyLayer | None, *, mpzp_from_kimpzp: bool
) -> dict[str, Fact[Any] | None]:
    """The planning facts of one site; empty when no plan ogólny in force covers it (stays "do sprawdzenia").

    `mpzp_from_kimpzp` is True when the site's zoning fact came from an MPZP answer (KIMPZP).
    """
    if layer is None or not layer.acts:
        return {}
    acts_tree: STRtree = layer._act_tree  # type: ignore[attr-defined]
    act_i = _dominant(site, acts_tree, [a.extent for a in layer.acts])
    if act_i is None:
        return {}
    act = layer.acts[act_i]

    def fact(value: Any, confidence: Confidence = Confidence.VERIFIED) -> Fact[Any]:
        return Fact(value, SOURCE_ID, layer.as_of, confidence)

    ouz_share = _share(site, layer.ouz)
    in_ouz = (
        IN_OUZ_YES
        if ouz_share >= OUZ_INSIDE_SHARE
        else IN_OUZ_NO
        if ouz_share <= OUZ_OUTSIDE_SHARE
        else IN_OUZ_PARTIAL
    )
    mpzp = mpzp_from_kimpzp or _share(site, layer.mpzp_in_force) >= MIN_OVERLAP_SHARE
    if mpzp:
        path = PATH_MPZP_IN_FORCE
    elif in_ouz == IN_OUZ_YES:
        path = PATH_WZ_POSSIBLE
    else:  # outside the OUZ, or only partly inside: a WZ cannot cover the whole site
        path = PATH_MPZP_OR_ZPI
    facts: dict[str, Fact[Any] | None] = {
        "in_ouz": fact(in_ouz),
        "planning_path": fact(path, Confidence.ESTIMATED),
        "plan_ogolny_act": fact(act.number or act.title),
    }
    if act.in_force_from:
        facts["plan_ogolny_from"] = fact(act.in_force_from)
    zones_tree: STRtree = layer._zone_tree  # type: ignore[attr-defined]
    zone_i = _dominant(site, zones_tree, [z.geom for z in layer.zones])
    if zone_i is not None:
        for key, name in _PARAM_FACTS.items():
            if key in layer.zones[zone_i].params:
                facts[name] = fact(layer.zones[zone_i].params[key])
    return facts
