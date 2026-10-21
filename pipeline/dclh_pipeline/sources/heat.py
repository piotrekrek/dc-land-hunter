"""District heating (proxy): towns holding a URE heat concession, for the `heat` bonus.

The URE concession register (`api.ure.gov.pl/api/ConcessionOtherFuel?rodzajKoncesji=PCC`, 344 licences, 200
from the VPS on 21.10.2026) names the town of each licence. It does not say where the pipes run, so this is a
proxy: a site near such a town may be near a heat network. Only the town, the voivodeship and the licence end
date are kept (the rows also carry the company's name, address, NIP and REGON, which the app does not need).

A town is placed on the map by its urban gmina (TERYT 7th digit `1`, gmina miejska): its PRG polygon is the
town. A concession town with no such gmina of that name in its voivodeship is not placed, and is left out (the
count is logged). Rural and mixed gminas are not used, because their polygons cover farmland and would put a
whole countryside at distance 0. A site then gets the distance to the nearest placed town; if none is placed,
the bonus is missing.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date
from typing import Any, ClassVar

import pandas as pd

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import GeomIndex, Layer
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

log = logging.getLogger(__name__)

CONCESSIONS_URL = "https://api.ure.gov.pl/api/ConcessionOtherFuel?rodzajKoncesji=PCC"
COLUMNS = ["town", "voivodeship", "valid_to"]
VOIVODESHIP_CODES: dict[str, str] = {
    "dolnośląskie": "02", "kujawsko-pomorskie": "04", "lubelskie": "06", "lubuskie": "08", "łódzkie": "10",
    "małopolskie": "12", "mazowieckie": "14", "opolskie": "16", "podkarpackie": "18", "podlaskie": "20",
    "pomorskie": "22", "śląskie": "24", "świętokrzyskie": "26", "warmińsko-mazurskie": "28",
    "wielkopolskie": "30", "zachodniopomorskie": "32",
}  # fmt: skip


class HeatConnector(Source):
    id: ClassVar[str] = "heat"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        resp = client.get(CONCESSIONS_URL)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("heat: the URE register returned no concessions; not saving an empty list")
        kept = [_reduce(row) for row in rows]
        content = json.dumps({"rows": kept}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return store.save(
            self.id, content, suffix=".json", url=CONCESSIONS_URL, content_type="application/json"
        )

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        doc = json.loads(snapshot.path.read_text(encoding="utf-8"))
        table = pd.DataFrame(doc["rows"], columns=COLUMNS)
        if table.empty:
            raise ValueError("heat: the saved snapshot has no concessions")
        if table[["town", "voivodeship"]].isna().any().any():
            raise ValueError("heat: the saved snapshot has rows without a town or voivodeship")
        return table


def _reduce(row: dict[str, Any]) -> dict[str, str]:
    """Only the fields the bonus needs. Names, addresses and tax ids stay out of the snapshot."""
    town = str(row.get("miejscowosc") or "").strip()
    voivodeship = str(row.get("wojewodztwo") or "").strip().lower()
    if not town or voivodeship not in VOIVODESHIP_CODES:
        raise ValueError(f"heat: concession {row.get('dkn')!r} has no usable town or voivodeship")
    return {"town": town, "voivodeship": voivodeship, "valid_to": str(row.get("dataDo") or "")}


def heat_town_layer(store: SnapshotStore, spec: SourceSpec, gminas: pd.DataFrame) -> Layer | None:
    """The placed concession towns as a layer, or None when no good snapshot exists.

    `gminas` is the PRG gmina table (`teryt`, `name`, `geometry`, EPSG:2180). Concessions that ended before
    the snapshot date are dropped: an expired licence says nothing about a heat network now.
    """
    snapshot = store.latest_good("heat")
    if snapshot is None:
        return None
    table = HeatConnector(spec).parse(snapshot)
    as_of = snapshot.fetched_at.astimezone(UTC).date()
    live = table[table["valid_to"].map(lambda text: _valid_on(text, as_of))]
    urban = gminas[gminas["teryt"].astype(str).str[-1] == "1"]
    polygons = []
    unplaced = 0
    for town, voivodeship in live[["town", "voivodeship"]].drop_duplicates().itertuples(index=False):
        code = VOIVODESHIP_CODES[voivodeship]
        hits = urban[(urban["name"] == town) & urban["teryt"].astype(str).str.startswith(code)]
        if hits.empty:
            unplaced += 1
            continue
        polygons.extend(hits.geometry.tolist())
    if unplaced:
        log.info("heat: %d concession towns have no urban gmina of the same name; not placed", unplaced)
    return Layer(
        source_id="heat",
        index=GeomIndex(polygons),
        as_of=snapshot.fetched_at,
        confidence=Confidence.ESTIMATED,
    )


def _valid_on(text: str, day: date) -> bool:
    """True when the licence has no end date or ends on or after `day` (dates are ISO, e.g. 2040-10-31)."""
    if not text:
        return True
    return date.fromisoformat(text[:10]) >= day
