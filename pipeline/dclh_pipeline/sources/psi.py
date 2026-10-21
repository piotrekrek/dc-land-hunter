"""Regional aid intensity (mapa pomocy regionalnej 2022–2027) for the `psi` bonus, from the regulation text.

The source is Rozporządzenie Rady Ministrów z 14.12.2021 (Dz.U. 2021 poz. 2422). `fetch()` stores the ELI text
(`api.sejm.gov.pl/eli/acts/DU/2021/2422/text.html`, answered 200 from the VPS on 21.10.2026). The rates
are the constants in `model/psi.py`; `parse()` checks each of them against the stored text, so a changed
regulation fails the run instead of scoring with old numbers. The rules name voivodeships and gminas, so
`load_psi_layer` needs the PRG gminas to place a site.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.psi import (
    CITY_POWIATS,
    PERCENT_PHRASES,
    VOIVODESHIP_TEXT,
    WARSAW_GMINY_PCT,
    PsiLayer,
    regulation_text,
)
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2021/2422/text.html"
WARSAW_REGION_PREFIX = "14"  # Mazowieckie: the Warsaw-area gminas are looked up here only


class PsiConnector(Source):
    id: ClassVar[str] = "psi"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        resp = client.get(ACT_URL)
        resp.raise_for_status()
        if "mapy pomocy regionalnej" not in regulation_text(resp.content).lower():
            raise ValueError(f"psi: {ACT_URL} does not look like the regional aid map regulation")
        return store.save(self.id, resp.content, suffix=".html", url=ACT_URL, content_type="text/html")

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """The rules as rows (`scope`, `pct`, `name`), after checking each one against the regulation text."""
        text = regulation_text(snapshot.path.read_bytes())
        missing = [p for p in PERCENT_PHRASES if p not in text]
        if missing:
            raise ValueError(f"psi: the regulation text no longer has {missing}; check the rules")
        rows: list[dict[str, object]] = []
        for pct, names in VOIVODESHIP_TEXT.items():
            absent = [n for n in names if n not in text]
            if absent:
                raise ValueError(f"psi: voivodeship names {absent} not in the regulation at {pct}%")
            rows += [{"scope": "voivodeship", "pct": pct, "name": n} for n in names]
        for pct, gminy in WARSAW_GMINY_PCT.items():
            absent = [g for g in gminy if g not in text]
            if absent:
                raise ValueError(f"psi: gminy {absent} not in the regulation at {pct}%")
            rows += [{"scope": "warsaw_gmina", "pct": pct, "name": g} for g in gminy]
        rows += [{"scope": "city", "pct": None, "name": name} for name in CITY_POWIATS.values()]
        return pd.DataFrame(rows, columns=["scope", "pct", "name"])


def load_psi_layer(store: SnapshotStore, spec: SourceSpec, gminas: pd.DataFrame) -> PsiLayer | None:
    """The psi rules for the run, or None when no good snapshot exists (the bonus is then missing).

    `gminas` is the PRG gmina table (`teryt`, `name`, `geometry`, EPSG:2180). Each listed Warsaw-area gmina
    name must match exactly one gmina of Mazowsze; otherwise the rule would land on the wrong place.
    """
    snapshot = store.latest_good("psi")
    if snapshot is None:
        return None
    PsiConnector(spec).parse(snapshot)  # checks the regulation text again; raises when it has changed
    warsaw: dict[str, int] = {}
    in_region = gminas[gminas["teryt"].astype(str).str.startswith(WARSAW_REGION_PREFIX)]
    for pct, names in WARSAW_GMINY_PCT.items():
        for name in names:
            matches = in_region.loc[in_region["name"] == name, "teryt"].astype(str).tolist()
            if len(matches) != 1:
                raise ValueError(
                    f"psi: gmina {name!r} matches {len(matches)} PRG gminas in Mazowsze, expected 1"
                )
            warsaw[matches[0]] = pct
    pairs = [(str(t), g) for t, g in zip(gminas["teryt"], gminas.geometry, strict=True)]
    return PsiLayer.build("psi", snapshot.fetched_at, pairs, warsaw)
