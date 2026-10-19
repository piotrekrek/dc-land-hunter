"""Tauron connector: paging, 429 handling, parsing the live sample (19.10.2026) and the published locations."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.tauron import TauronConnector

ClientFactory = Callable[..., PoliteClient]
SPEC = SourceSpec(id="tauron", name="Tauron", used_for="t", plan="mvp", cadence="weekly")
SAMPLE = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19/tauron_gpz_search.json"


def _sample() -> dict[str, Any]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _matches() -> list[dict[str, Any]]:
    return list(_sample()["data"]["matches"])  # 5 real GPZs; the live total is 513


def _page(matches: list[dict[str, Any]], total: int, offset: int) -> dict[str, Any]:
    page = _sample()
    page["data"].update({"matches": matches, "total": total, "offset": offset})
    return page


def _snapshot(store: SnapshotStore, pages: list[dict[str, Any]]) -> Any:
    content = json.dumps({"year": 2026, "pages": pages}).encode()
    return store.save("tauron", content, suffix=".json")


def test_live_sample_parses_with_locations_in_2180(store: SnapshotStore) -> None:
    matches = _matches()
    table = TauronConnector(SPEC).parse(_snapshot(store, [_page(matches, len(matches), 0)]))
    assert list(table["gpz_code"]) == ["BRD3", "OLW3", "STC3", "STP3", "BOW3"]
    first = table.iloc[0]
    assert (first["gpz_name"], first["published_mw"], first["group_name"]) == (
        "Bierdzany",
        2.2,
        "Blachownia 1",
    )
    assert first["note"] == "grupa Blachownia 1"
    assert set(table["dso"]) == {"tauron"}
    # Bierdzany lies about 20 km north-east of Opole, which is near (424 km, 312 km) in EPSG:2180;
    # swapped axes would put it hundreds of kilometres away
    assert 0 < first["x_2180"] - 424_000 < 30_000
    assert 0 < first["y_2180"] - 312_000 < 30_000
    assert table["published_mw"].iloc[1] == 0.0  # a published zero is a real value


def test_rows_must_add_up_to_the_reported_total(store: SnapshotStore) -> None:
    with pytest.raises(ValueError, match="5 GPZs in the pages, but the server reported 513"):
        TauronConnector(SPEC).parse(_snapshot(store, [_sample()]))


def test_duplicate_codes_and_points_outside_poland_are_rejected(store: SnapshotStore) -> None:
    matches = _matches()
    twice = [*matches, copy.deepcopy(matches[0])]
    with pytest.raises(ValueError, match="codes listed twice: \\['BRD3'\\]"):
        TauronConnector(SPEC).parse(_snapshot(store, [_page(twice, len(twice), 0)]))
    far = copy.deepcopy(matches[:1])
    far[0]["location"] = {"lat": 48.0, "lng": 2.3}
    with pytest.raises(ValueError, match="outside Poland"):
        TauronConnector(SPEC).parse(_snapshot(store, [_page(far, 1, 0)]))


def test_missing_value_is_not_published_and_external_gpz_is_noted(store: SnapshotStore) -> None:
    matches = _matches()[:1]
    matches[0]["value_mw"] = None
    matches[0]["is_external"] = True
    table = TauronConnector(SPEC).parse(_snapshot(store, [_page(matches, 1, 0)]))
    assert math.isnan(table["published_mw"].iloc[0])
    assert table["note"].iloc[0] == "grupa Blachownia 1; GPZ innego operatora"


def test_fetch_pages_until_the_total_and_waits_on_429(
    store: SnapshotStore, make_client: ClientFactory, fake_clock: Any
) -> None:
    matches = _matches()
    bodies: list[dict[str, Any]] = []
    rate_limited = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if not rate_limited["done"]:
            rate_limited["done"] = True
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"code": "API_RATE_LIMITED"})
        body = json.loads(request.content)
        bodies.append(body)
        offset = body["page"]["offset"]
        chunk = matches[offset : offset + 3]  # the server pages in threes here
        return httpx.Response(200, json=_page(chunk, len(matches), offset))

    client = make_client(handler)
    snapshot = TauronConnector(SPEC).fetch(client, store)
    assert [b["page"]["offset"] for b in bodies] == [0, 3]
    assert {b["layer_type"] for b in bodies} == {"odbiorcy"}
    assert len(TauronConnector(SPEC).parse(snapshot)) == len(matches)
    assert 7.0 in fake_clock.sleeps  # honoured Retry-After
