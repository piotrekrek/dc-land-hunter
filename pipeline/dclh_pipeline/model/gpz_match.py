"""Match DSO capacity lists to GPZ substations by name (brief section 5, step 2; plan Phase 3).

DSO capacity maps name a GPZ ("GPZ Piaski"), while the grid comes from OSM substations. Names are
normalised (diacritics folded, the "GPZ"/"stacja" prefixes dropped) and compared with a fuzzy ratio. A
match is used only when it is clearly the best one: below the threshold, or tied with another station, it
stays unmatched and the reason is kept, so the match rate in the report is honest. A manual mapping
(`data/manual/gpz_mapping.csv`) always wins.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process
from shapely.geometry import Point

from dclh_pipeline.model.features import Substation
from dclh_pipeline.model.grid import SubstationClass

# A second candidate this close to the best one makes the match ambiguous, so it is not used.
AMBIGUITY_MARGIN = 2.0

# Words that name the kind of object, not the place: "GPZ Piaski" and "Piaski" are the same station.
_NOISE = re.compile(r"\b(gpz|rs|ps|stacja|stacji|stacja elektroenergetyczna|110 kv|sn)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GpzCandidate:
    """A GPZ that a DSO name can be matched to.

    Holds the OSM substation id, its name and its location (EPSG:2180).
    """

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


def gpz_candidates(substations: Iterable[Substation]) -> list[GpzCandidate]:
    """The 110 kV GPZs that have an OSM name. Unnamed stations cannot be matched, so they are left out."""
    out: list[GpzCandidate] = []
    for sub in substations:
        if sub.cls is not SubstationClass.GPZ_110_SN or not sub.name:
            continue
        if not isinstance(sub.geom, Point):
            raise ValueError(f"substation {sub.osm_id} geometry is {sub.geom.geom_type}, expected Point")
        out.append(GpzCandidate(sub.osm_id, sub.name, sub.geom))
    return out


def match_gpz_names(
    dso: str,
    dso_names: Sequence[str],
    candidates: Sequence[GpzCandidate],
    *,
    manual: Mapping[str, int] | None = None,
    min_score: float = 88.0,
) -> list[MatchResult]:
    """Match each DSO GPZ name to one candidate, or to none (kept with a reason).

    `manual` maps a normalised DSO name to an OSM substation id. A manual id that is not among the candidates
    raises: the mapping file is maintained by hand, so a stale id must fail loudly, not be dropped.
    """
    manual = manual or {}
    by_key = {c.key: c for c in candidates}
    norm_candidates = [normalise_name(c.name) for c in candidates]
    keys = [c.key for c in candidates]
    results: list[MatchResult] = []
    for name in dso_names:
        norm = normalise_name(name)
        if norm in manual:
            key = manual[norm]
            if key not in by_key:
                raise ValueError(
                    f"gpz_mapping.csv: {dso} {name!r} maps to OSM id {key}, which is not a GPZ candidate"
                )
            results.append(MatchResult(dso, name, key, 100.0, "manual", "ręczne mapowanie"))
            continue
        results.append(_fuzzy(dso, name, norm, norm_candidates, keys, min_score))
    return results


def _fuzzy(
    dso: str,
    name: str,
    norm: str,
    norm_candidates: list[str],
    keys: list[int],
    min_score: float,
) -> MatchResult:
    if not norm or not norm_candidates:
        return MatchResult(dso, name, None, 0.0, "none", "brak nazwy do dopasowania")
    best = process.extract(norm, norm_candidates, scorer=fuzz.ratio, limit=2)
    top_name, top_score, top_idx = best[0]
    if top_score < min_score:
        return MatchResult(
            dso, name, None, top_score, "none", f"najlepsza zgodność {top_score:.0f} < {min_score:.0f}"
        )
    if len(best) > 1 and best[1][1] >= min_score and best[1][1] >= top_score - AMBIGUITY_MARGIN:
        return MatchResult(
            dso, name, None, top_score, "none", "niejednoznaczne: kilka stacji o podobnej nazwie"
        )
    return MatchResult(dso, name, keys[top_idx], top_score, "fuzzy", f"nazwa OSM: {top_name}")


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
