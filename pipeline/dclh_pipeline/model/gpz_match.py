"""Match DSO capacity lists to GPZ substations by name (brief section 5, step 2; plan Phase 3).

DSO capacity maps name a GPZ ("GPZ Piaski"), while the grid comes from OSM substations. Names are
normalised (diacritics folded, the "GPZ"/"stacja" prefixes dropped) and compared with a fuzzy ratio. A
match is used only when it is clearly the best one: below the threshold, or tied with a station somewhere
else, it stays unmatched and the reason is kept, so the match rate in the report is honest.

Two rules guard against wrong matches. Numbers in a name must be identical ("Gdańsk 10" is not
"Gdańsk 1"), because a fuzzy ratio barely notices a digit. And stations that tie but lie within a few
hundred metres of each other are one physical station mapped twice in OSM (a node and an outline), not an
ambiguity.

A manual mapping (`data/manual/gpz_mapping.csv`) always wins, and may point at any substation, including
one that has no name or no voltage tag, since those are exactly the ones fuzzy matching cannot find.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process
from shapely.geometry import Point

from dclh_pipeline.model.features import Substation
from dclh_pipeline.model.grid import SubstationClass

# A second candidate this close to the best one makes the match ambiguous, unless it is the same station.
AMBIGUITY_MARGIN = 2.0
# Tied stations closer than this are taken to be one station mapped twice in OSM.
SAME_STATION_RADIUS_M = 500.0

# Words that name the kind of object, not the place: "GPZ Piaski" and "Piaski" are the same station. The
# longest phrase comes first so that "stacja" does not eat the start of "stacja elektroenergetyczna".
_NOISE = re.compile(r"\b(stacja elektroenergetyczna|stacja|stacji|gpz|rs|ps|110 \d{1,2}|110 kv|kv|sn)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class GpzCandidate:
    """A substation that a DSO name can be matched to: the OSM key, its name and its location (EPSG:2180)."""

    key: int
    name: str
    geom: Point


@dataclass(frozen=True)
class MatchResult:
    dso: str
    dso_gpz_name: str
    candidate_key: int | None
    score: float  # 0–100; 100 for a manual mapping, 0 when nothing was found
    method: Literal["manual", "fuzzy", "none"]
    reason: str  # Polish, shown in the match report


def normalise_name(name: str) -> str:
    """Fold diacritics and case, keep letters and digits, drop generic words.

    An empty result means "no name".
    """
    folded = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L"))
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    spaced = _NON_ALNUM.sub(" ", ascii_only)
    without_noise = _NOISE.sub(" ", spaced)
    return " ".join(without_noise.split())


def _point(sub: Substation) -> Point:
    if not isinstance(sub.geom, Point):
        raise ValueError(f"substation {sub.osm_id} geometry is {sub.geom.geom_type}, expected Point")
    return sub.geom


def gpz_candidates(substations: Iterable[Substation]) -> list[GpzCandidate]:
    """The 110 kV GPZs that have an OSM name: the stations fuzzy matching can work with."""
    return [
        GpzCandidate(sub.osm_id, sub.name, _point(sub))
        for sub in substations
        if sub.cls is SubstationClass.GPZ_110_SN and sub.name
    ]


def station_points(substations: Iterable[Substation]) -> list[GpzCandidate]:
    """Every substation (named or not, any class): the targets a manual mapping may point at."""
    return [GpzCandidate(sub.osm_id, sub.name or "", _point(sub)) for sub in substations]


def match_gpz_names(
    dso: str,
    dso_names: Sequence[str],
    candidates: Sequence[GpzCandidate],
    *,
    manual: Mapping[str, int] | None = None,
    manual_targets: Collection[int] = (),
    min_score: float = 88.0,
) -> list[MatchResult]:
    """Match each DSO GPZ name to one candidate, or to none (kept with a reason).

    `manual` maps a normalised DSO name to an OSM substation key. A manual key must be among the candidates
    or `manual_targets`; otherwise it raises, because the mapping file is maintained by hand and a stale id
    must fail loudly, not be dropped.
    """
    manual = manual or {}
    valid_manual_keys = {c.key for c in candidates} | set(manual_targets)
    norm_candidates = [normalise_name(c.name) for c in candidates]
    results: list[MatchResult] = []
    for name in dso_names:
        norm = normalise_name(name)
        if norm in manual:
            key = manual[norm]
            if key not in valid_manual_keys:
                raise ValueError(
                    f"gpz_mapping.csv: {dso} {name!r} maps to OSM id {key}, which is not a known substation"
                )
            results.append(MatchResult(dso, name, key, 100.0, "manual", "ręczne mapowanie"))
            continue
        results.append(_fuzzy(dso, name, norm, candidates, norm_candidates, min_score))
    return results


def _fuzzy(
    dso: str,
    name: str,
    norm: str,
    candidates: Sequence[GpzCandidate],
    norm_candidates: Sequence[str],
    min_score: float,
) -> MatchResult:
    if not norm or not norm_candidates:
        return MatchResult(dso, name, None, 0.0, "none", "brak nazwy do dopasowania")
    # Numbers must be identical: "gdansk 10" must not match "gdansk 1" at ratio 94.
    wanted = _DIGITS.findall(norm)
    pool = [i for i, cand in enumerate(norm_candidates) if _DIGITS.findall(cand) == wanted]
    if not pool:
        return MatchResult(dso, name, None, 0.0, "none", "brak stacji o tym samym numerze w nazwie")
    scored = process.extract(norm, [norm_candidates[i] for i in pool], scorer=fuzz.ratio, limit=5)
    ranked = sorted(
        ((score, candidates[pool[idx]]) for _, score, idx in scored),
        key=lambda item: (-item[0], item[1].key),
    )
    top_score, top = ranked[0]
    if top_score < min_score:
        reason = f"najlepsza zgodność {top_score:.0f} < {min_score:.0f}"
        return MatchResult(dso, name, None, top_score, "none", reason)
    contenders = [
        c for score, c in ranked[1:] if score >= min_score and score >= top_score - AMBIGUITY_MARGIN
    ]
    if any(c.geom.distance(top.geom) > SAME_STATION_RADIUS_M for c in contenders):
        return MatchResult(
            dso, name, None, top_score, "none", "niejednoznaczne: kilka stacji o podobnej nazwie"
        )
    return MatchResult(dso, name, top.key, top_score, "fuzzy", f"nazwa OSM: {top.name}")


def match_summary(results: Sequence[MatchResult]) -> dict[str, int | float]:
    """Counts for the match report: how many DSO names were matched, by which method, and the rate."""
    total = len(results)
    matched = sum(1 for r in results if r.candidate_key is not None)
    manual = sum(1 for r in results if r.method == "manual")
    return {
        "total": total,
        "matched": matched,
        "manual": manual,
        "fuzzy": matched - manual,
        "rate": matched / total if total else 0.0,
    }
