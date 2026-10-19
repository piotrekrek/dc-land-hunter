"""Tauron Dystrybucja: published load capacity per GPZ from dostepnemoce.tauron-dystrybucja.pl (Phase 3).

The map on the portal reads a JSON search endpoint (verified 19.10.2026, `docs/SOURCES.md`). One POST per page
of 100 GPZs; about 513 GPZs in six requests. Each GPZ has a code, a name, the group of GPZs that share
capacity, the value in MW for the requested year and its location (WGS84). The portal calls the values
"orientacyjne" (indicative), so they are a signal, not a promise.

The terms were read on 19.10.2026: the portal has no terms of its own and robots.txt allows everything. The
Tauron service rules (`tauron-dystrybucja.pl/regulamin-swiadczenia-uslug`, §11) only forbid overloading
their systems, so the connector runs weekly at the polite client's rate and honours 429 / Retry-After.

Because Tauron publishes the location, a GPZ does not need to be matched to OSM by name: the capacity layer
uses the published point directly.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pyproj import Transformer

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import EPSG, POLAND_BOUNDS

SEARCH_URL = "https://dostepnemoce.tauron-dystrybucja.pl/wp-json/tauron-connector/v1/gpz/search"
PAGE_LIMIT = 100
MAX_PAGES = 50  # 5,000 GPZs: far above the 513 published; stops a server that never reports the end
COLUMNS = ["dso", "gpz_name", "published_mw", "as_of", "note", "gpz_code", "group_name", "x_2180", "y_2180"]
_WGS84_TO_2180 = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True)


def search_body(year: int, offset: int) -> dict[str, Any]:
    """The request the portal's own map sends, for loads ("odbiorcy") in one year, all provinces."""
    return {
        "layer_type": "odbiorcy",
        "criteria": {"minimum_mw": 0, "year_from": year, "year_to": year, "province": ""},
        "page": {"offset": offset, "limit": PAGE_LIMIT},
    }


class TauronConnector(Source):
    id = "tauron"

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        """Fetch every page for the current year and store them together as one JSON snapshot."""
        year = datetime.now(UTC).year
        pages: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            resp = client.request("POST", SEARCH_URL, json=search_body(year, offset))
            resp.raise_for_status()
            page = resp.json()
            pages.append(page)
            data = page["data"]
            offset += len(data["matches"])
            if not data["matches"] or offset >= int(data["total"]):
                break
        else:
            raise ValueError(f"tauron: more than {MAX_PAGES} pages; the paging rules may have changed")
        content = json.dumps({"year": year, "pages": pages}, ensure_ascii=False, sort_keys=True)
        return store.save(self.id, content.encode("utf-8"), suffix=".json", url=SEARCH_URL)

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """One row per GPZ: the capacity columns of `sources/manual.py` plus code, group and location."""
        doc = json.loads(snapshot.path.read_text(encoding="utf-8"))
        pages = doc["pages"]
        total = int(pages[0]["data"]["total"]) if pages else 0
        as_of = pd.Timestamp(snapshot.fetched_at.date())
        rows = [_row(m, as_of) for page in pages for m in page["data"]["matches"]]
        if len(rows) != total:
            raise ValueError(f"tauron: {len(rows)} GPZs in the pages, but the server reported {total}")
        table = pd.DataFrame(rows, columns=COLUMNS)
        duplicated = table["gpz_code"].duplicated(keep=False)
        if duplicated.any():
            raise ValueError(
                f"tauron: GPZ codes listed twice: {sorted(set(table.loc[duplicated, 'gpz_code']))}"
            )
        table["published_mw"] = table["published_mw"].astype("float64")
        return table


def _row(match: dict[str, Any], as_of: pd.Timestamp) -> dict[str, object]:
    code, name = str(match["gpz_code"]), str(match["name"]).strip()
    if not name:
        raise ValueError(f"tauron: GPZ {code} has no name")
    mw = match.get("value_mw")
    value = float("nan") if mw is None else float(mw)
    if not (math.isnan(value) or (math.isfinite(value) and value >= 0)):
        raise ValueError(f"tauron: GPZ {code} has value_mw {mw!r}")
    location = match["location"]
    x, y = _WGS84_TO_2180.transform(float(location["lng"]), float(location["lat"]))
    x0, y0, x1, y1 = POLAND_BOUNDS
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        raise ValueError(f"tauron: GPZ {code} lies outside Poland ({location})")
    group = str(match.get("group_name") or "")
    note = f"grupa {group}" if group else ""
    if match.get("is_external"):
        note = f"{note}; GPZ innego operatora".lstrip("; ")
    return {
        "dso": "tauron",
        "gpz_name": name,
        "published_mw": value,
        "as_of": as_of,
        "note": note,
        "gpz_code": code,
        "group_name": group,
        "x_2180": float(x),
        "y_2180": float(y),
    }
