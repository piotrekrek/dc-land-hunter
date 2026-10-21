"""Regional aid intensity rules (mapa pomocy regionalnej 2022–2027) for the `psi` bonus.

Pure rules, no I/O. The regulation text is read by `sources/psi.py`, which checks every constant here
against it. The rules: § 3 ust. 1 sets the maximum intensity by voivodeship (50 / 40 / 30 / 25 %), with
exceptions for the Warsaw regional area (listed gminas, 25 or 35 %) and for Poznań, Wrocław and the Poznań
subregion (15–20 % in time). A site gets the rate of the gmina that covers most of it, and points for that
rate (see `psi_points_fact`).

Not modelled, so a site is missing (never 0 and never a guessed rate):
- the Warsaw regional area's own boundary: a gmina of Mazowsze that is not in the listed gminas is unknown;
  the Siedlce subregion (50 %) is treated the same way;
- the Poznań subregion (podregion poznański): every gmina of Wielkopolska outside Poznań is unknown;
- the just-transition (FST) +10 points of § 5, whose areas come from separate decisions. A rate may be too low
  in such an area, so the fact is `estimated`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from shapely import STRtree
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import ScoringConfig
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring.types import Fact

# § 3 ust. 1 pkt 1–4: the base maximum by voivodeship TERYT code (the first two digits of a gmina's code).
VOIVODESHIP_PCT: dict[str, int] = {
    "06": 50,
    "18": 50,
    "20": 50,
    "26": 50,
    "28": 50,  # lubelskie, podkarpackie, podlaskie, świętokrzyskie, warmińsko-mazurskie
    "04": 40,
    "08": 40,
    "10": 40,
    "12": 40,
    "16": 40,
    "32": 40,  # kujawsko-pomorskie, lubuskie, łódzkie, małopolskie, opolskie, zachodniopomorskie
    "22": 30,
    "24": 30,  # pomorskie, śląskie
    "02": 25,  # dolnośląskie (Wrocław: see CITY_POWIATS)
}
# The regulation's genitive names of the voivodeships above, as printed in the text (checked by parse()).
VOIVODESHIP_TEXT: dict[int, tuple[str, ...]] = {
    50: ("lubelskiego", "podkarpackiego", "podlaskiego", "świętokrzyskiego", "warmińsko-mazurskiego"),
    40: (
        "kujawsko-pomorskiego",
        "lubuskiego",
        "łódzkiego",
        "małopolskiego",
        "opolskiego",
        "zachodniopomorskiego",
    ),
    30: ("pomorskiego", "śląskiego"),
    25: ("dolnośląskiego", "wielkopolskiego"),
}
# § 3 ust. 1 pkt 5: Poznań and Wrocław (city powiats, TERYT 3064 and 0264) and the Poznań subregion.
CITY_POWIATS: dict[str, str] = {"0264": "Wrocław", "3064": "Poznań"}
CITY_PCT_BY_YEARS: tuple[tuple[range, int], ...] = ((range(2022, 2025), 20), (range(2025, 2028), 15))
# § 3 ust. 2: the Warsaw regional area, by gmina name. The names are matched to TERYT codes in Mazowsze only.
WARSAW_GMINY_PCT: dict[int, tuple[str, ...]] = {
    25: (
        "Baranów",
        "Błonie",
        "Góra Kalwaria",
        "Grodzisk Mazowiecki",
        "Jaktorów",
        "Kampinos",
        "Leoncin",
        "Leszno",
        "Nasielsk",
        "Prażmów",
        "Tarczyn",
        "Zakroczym",
        "Żabia Wola",
    ),
    35: (
        "Dąbrówka",
        "Dobre",
        "Jadów",
        "Kałuszyn",
        "Kołbiel",
        "Latowicz",
        "Mrozy",
        "Osieck",
        "Serock",
        "Siennica",
        "Sobienie-Jeziory",
        "Strachówka",
        "Tłuszcz",
    ),
}
# Phrases that must be in the regulation text for the constants above to be current.
PERCENT_PHRASES: tuple[str, ...] = (
    "50% - na obszarach należących do województw",
    "40% - na obszarach należących do województw",
    "30% - na obszarach należących do województw",
    "25% - na obszarach należących do województw",
    "20% - w okresie od dnia 1 stycznia 2022",
    "15% - w okresie od dnia 1 stycznia 2025",
    "25% - na obszarach należących do gmin:",
    "35% - na obszarach należących do gmin:",
)

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def regulation_text(raw_html: bytes) -> str:
    """The regulation as plain text with single spaces, so phrases split over lines still match."""
    text = _TAG.sub(" ", raw_html.decode("utf-8", errors="replace"))
    return _SPACE.sub(" ", html.unescape(text))


def city_pct(year: int) -> int | None:
    """The Poznań and Wrocław maximum for a year (§ 3 ust. 1 pkt 5), or None outside 2022–2027."""
    for years, pct in CITY_PCT_BY_YEARS:
        if year in years:
            return pct
    return None


def intensity_pct(gmina_teryt: str, year: int, warsaw: Mapping[str, int]) -> int | None:
    """The maximum regional aid intensity (%) for a gmina, or None when the rule cannot be applied.

    `warsaw` maps the TERYT codes of the listed Warsaw-area gminas to their rate (25 or 35). Every gmina of
    Mazowsze that is not listed, and every gmina of Wielkopolska outside Poznań, is None: the regulation
    depends on subregion lists this code does not have yet.
    """
    voivodeship, powiat = gmina_teryt[:2], gmina_teryt[:4]
    if powiat in CITY_POWIATS:
        return city_pct(year)
    if voivodeship == "14":
        return warsaw.get(gmina_teryt)
    if voivodeship == "30":
        return None
    return VOIVODESHIP_PCT.get(voivodeship)


@dataclass(frozen=True)
class PsiLayer:
    """The regional aid rules for one run: gmina polygons (TERYT, EPSG:2180) and the listed Warsaw gminas."""

    source_id: str
    as_of: datetime
    year: int  # the calendar year the rates are read for (Poznań and Wrocław change on 1 January 2025)
    warsaw: dict[str, int]  # TERYT of a listed Warsaw-area gmina -> its rate
    gminy: tuple[tuple[str, BaseGeometry], ...]
    tree: STRtree

    @classmethod
    def build(
        cls,
        source_id: str,
        as_of: datetime,
        gminy: Sequence[tuple[str, BaseGeometry]],
        warsaw: dict[str, int],
    ) -> PsiLayer:
        geoms = tuple(gminy)
        tree = STRtree([g for _, g in geoms]) if geoms else STRtree([])
        return cls(source_id, as_of, as_of.year, dict(warsaw), geoms, tree)


def psi_points_fact(site: BaseGeometry, layer: PsiLayer | None, cfg: ScoringConfig) -> Fact[float] | None:
    """Bonus points for the regional aid rate of the gmina covering most of the site.

    Points scale linearly with the rate: `psi_aid_max_pts` at `psi_aid_full_pct` (the top of the map) and
    less below it, capped at the maximum. Missing (None) when the layer is not loaded, the site lies in no
    gmina, or the rule cannot be applied (see the module docstring).
    """
    if layer is None or not layer.gminy:
        return None
    best: tuple[float, str] | None = None
    for i in layer.tree.query(site, predicate="intersects"):
        teryt, geom = layer.gminy[int(i)]
        overlap = site.intersection(geom).area
        if best is None or overlap > best[0]:
            best = (overlap, teryt)
    if best is None:
        return None
    pct = intensity_pct(best[1], layer.year, layer.warsaw)
    if pct is None:
        return None
    max_pts = cfg.bonus.psi_aid_max_pts
    points = min(max_pts, max_pts * pct / cfg.bonus.psi_aid_full_pct)
    return Fact(value=points, source_id=layer.source_id, as_of=layer.as_of, confidence=Confidence.ESTIMATED)
