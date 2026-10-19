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
from dclh_pipeline.model.grid import Dso, SubstationClass, derive_operator

_DSO_IDS = frozenset(d.value for d in Dso if d is not Dso.PSE)

# A second candidate this close to the best one makes the match ambiguous, unless it is the same station.
AMBIGUITY_MARGIN = 2.0
# Tied stations closer than this are taken to be one station mapped twice in OSM.
SAME_STATION_RADIUS_M = 500.0

# Voltage specs, as OSM writes them inside names: "110/15kV", "400/220/110 kV", "220kV", or "110/15" alone.
# They must go before the digit rule below, or "Stacja 110/15kV Kutno" would never match "Kutno".
_VOLTAGE = re.compile(r"\b\d{1,3}(?:\s*/\s*\d{1,3})*\s*kv\b|\b(?:400|220|110)(?:\s*/\s*\d{1,3})+\b")
# Words that name the kind of object, not the place: "GPZ Piaski" and "Piaski" are the same station. The
# longest phrase comes first so that "stacja" does not eat the start of "stacja elektroenergetyczna".
# "SE"/"RPZ" are prefixes like "GPZ" ("GPO", a wind farm's own station, is not: it is another station);
# "Systemowa" marks a PSE station ("Baczyna Systemowa" is OSM's
# "Baczyna"); "sekcja 2" is a busbar section of one station (Energa lists "Kutno sekcja 1" and "sekcja 2").
_NOISE = re.compile(
    r"\b(stacja elektroenergetyczna|podstacja|stacja|stacji|gpz|rpz|se|rs|ps|systemowa|sekcja \d+"
    r"|110 \d{1,2}|110 kv|kv|sn)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# A trailing roman numeral is a number: Energa writes "Gdańsk 2" and "Olsztyn 1",
# OSM "Gdańsk II" and "Olsztyn I".
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class GpzCandidate:
    """A substation that a DSO name can be matched to: the OSM key, its name and its location (EPSG:2180)."""

    key: int
    name: str
    geom: Point
    operator: Dso | None = None  # from the OSM `operator` tag; None when untagged or not a DSO/PSE


@dataclass(frozen=True)
class MatchResult:
    dso: str
    dso_gpz_name: str
    candidate_key: int | None
    score: float  # 0–100; 100 for a manual mapping or a published location, 0 when nothing was found
    # "published": the DSO gives the location itself (Tauron), so no name matching is needed
    method: Literal["manual", "fuzzy", "published", "none"]
    reason: str  # Polish, shown in the match report


def normalise_name(name: str) -> str:
    """Fold diacritics and case, keep letters and digits, drop generic words.

    An empty result means "no name".
    """
    folded = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L"))
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    spaced = _NON_ALNUM.sub(" ", _VOLTAGE.sub(" ", ascii_only))
    words = _NOISE.sub(" ", spaced).split()
    if len(words) > 1 and words[-1] in _ROMAN:
        words[-1] = _ROMAN[words[-1]]
    return " ".join(words)


def _point(sub: Substation) -> Point:
    if not isinstance(sub.geom, Point):
        raise ValueError(f"substation {sub.osm_id} geometry is {sub.geom.geom_type}, expected Point")
    return sub.geom


def gpz_candidates(substations: Iterable[Substation]) -> list[GpzCandidate]:
    """The named 110 kV GPZs and 220/400 kV nodes (a DSO GPZ can sit inside a PSE station, e.g. Dunowo)."""
    return [
        GpzCandidate(sub.osm_id, sub.name, _point(sub), derive_operator(sub.operator))
        for sub in substations
        if sub.cls in (SubstationClass.GPZ_110_SN, SubstationClass.NODE_220_400) and sub.name
    ]


def node_candidates(substations: Iterable[Substation]) -> list[GpzCandidate]:
    """The named 220/400 kV nodes: where the PSE lists' stations are looked up."""
    return [
        GpzCandidate(sub.osm_id, sub.name, _point(sub), derive_operator(sub.operator))
        for sub in substations
        if sub.cls is SubstationClass.NODE_220_400 and sub.name
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
    min_score: float = 90.0,
) -> list[MatchResult]:
    """Match each DSO GPZ name to one candidate, or to none (kept with a reason).

    `manual` maps a normalised DSO name to an OSM substation key. A manual key must be among the candidates
    or `manual_targets`; otherwise it raises, because the mapping file is maintained by hand and a stale id
    must fail loudly, not be dropped.
    """
    manual = manual or {}
    valid_manual_keys = {c.key for c in candidates} | set(manual_targets)
    if dso in _DSO_IDS:
        # A DSO lists its own stations: one tagged with another DSO is a namesake elsewhere ("Bojanowo" of
        # Enea is not Energa's). Untagged stations and PSE nodes (a GPZ inside a PSE station) stay in.
        candidates = [c for c in candidates if c.operator in (None, Dso.PSE) or c.operator.value == dso]
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
    return _one_name_per_station(results)


def _one_name_per_station(results: list[MatchResult]) -> list[MatchResult]:
    """A station claimed by several fuzzy names keeps only the best one ("Ostróda Wschód" at 100 beats
    "Ostrów Wschód" at 89 for the same OSM station); the others become unmatched. Manual mappings are kept."""
    best: dict[int, float] = {}
    for r in results:
        if r.method == "fuzzy" and r.candidate_key is not None:
            best[r.candidate_key] = max(best.get(r.candidate_key, 0.0), r.score)
    out: list[MatchResult] = []
    for r in results:
        if r.method == "fuzzy" and r.candidate_key is not None and r.score < best[r.candidate_key]:
            r = MatchResult(
                r.dso, r.dso_gpz_name, None, r.score, "none", "stacja OSM przypisana lepiej pasującej nazwie"
            )
        out.append(r)
    return out


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
    matched = sum(1 for r in results if r.method != "none")
    manual = sum(1 for r in results if r.method == "manual")
    published = sum(1 for r in results if r.method == "published")
    return {
        "total": total,
        "matched": matched,
        "manual": manual,
        "published": published,
        "fuzzy": matched - manual - published,
        "rate": matched / total if total else 0.0,
    }
