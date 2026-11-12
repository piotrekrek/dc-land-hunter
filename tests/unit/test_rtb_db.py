"""Power evidence and the RTB export against a real PostGIS database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_pipeline.db import session_scope
from tests.unit.test_sites_db import _site_id, ann, bob, seeded  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]


@pytest.fixture(autouse=True)
def _no_evidence() -> Iterator[None]:
    with session_scope() as session:
        session.execute(text("DELETE FROM power_evidence"))
    yield
    # Leave no sites behind: the next DB module's guard refuses a database whose sites table has rows.
    with session_scope() as session:
        session.execute(text("DELETE FROM power_evidence"))
        session.execute(text("DELETE FROM sites"))


WORKSTREAMS = ["land", "power", "planning", "environment", "concept", "commercial"]


def test_no_row_reads_rumour_and_is_not_recorded(seeded: None, ann: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    evidence = ann.get(f"/api/sites/{sid}").json()["power_evidence"]
    assert evidence["level"] == "rumour" and evidence["recorded"] is False


def test_evidence_is_saved_shared_and_idempotent(seeded: None, ann: TestClient, bob: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    body = {
        "level": "conditions_issued",
        "document_date": "2026-10-01",
        "conditions_expiry": "2027-10-01",
        "attachment_url": "https://drive.example.org/f/1",
    }
    first = ann.put(f"/api/sites/{sid}/power-evidence", json=body)
    assert first.status_code == 200
    assert ann.put(f"/api/sites/{sid}/power-evidence", json=body).json()["level"] == "conditions_issued"
    seen = bob.get(f"/api/sites/{sid}").json()["power_evidence"]
    assert seen["recorded"] is True and seen["conditions_expiry"] == "2027-10-01"
    assert seen["attachment_url"] == "https://drive.example.org/f/1"
    with session_scope() as session:
        count = session.execute(text("SELECT count(*) FROM power_evidence WHERE site_id = :s"), {"s": sid})
        assert count.scalar_one() == 1


def test_bad_evidence_is_refused(seeded: None, ann: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    url = f"/api/sites/{sid}/power-evidence"
    assert ann.put(url, json={"level": "rumour", "attachment_url": "javascript:alert(1)"}).status_code == 422
    assert ann.put(url, json={"level": "rumour", "conditions_expiry": "2027-01-01"}).status_code == 422
    assert ann.put(url, json={"level": "promise"}).status_code == 422
    assert ann.put("/api/sites/nope/power-evidence", json={"level": "rumour"}).status_code == 404
    assert ann.get(f"/api/sites/{sid}").json()["power_evidence"]["recorded"] is False


def test_delete_returns_to_rumour_and_is_idempotent(seeded: None, ann: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    ann.put(f"/api/sites/{sid}/power-evidence", json={"level": "agreement_signed"})
    assert ann.delete(f"/api/sites/{sid}/power-evidence").status_code == 204
    assert ann.delete(f"/api/sites/{sid}/power-evidence").status_code == 204
    assert ann.get(f"/api/sites/{sid}").json()["power_evidence"]["level"] == "rumour"


def test_rtb_export_has_every_part(seeded: None, ann: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    out = ann.get(f"/api/sites/{sid}/rtb")
    assert out.status_code == 200
    rtb = out.json()
    assert [w["key"] for w in rtb["workstreams"]] == WORKSTREAMS
    assert {w["status"] for w in rtb["workstreams"]} <= {"green", "yellow", "red"}
    power = next(w for w in rtb["workstreams"] if w["key"] == "power")
    assert power["status"] != "green" and rtb["power_evidence"]["level"] == "rumour"
    # No RCN transactions imported in the test database: the slot is there, empty and marked missing.
    prices = rtb["price_comparables"]
    assert prices["comparables"]["confidence"] == "missing"
    assert prices["comparables"]["agricultural"]["count"] == 0 and prices["asking_zl_m2"] is None
    assert rtb["map"]["layers"] and rtb["map"]["parcel"]
    cost = rtb["connection_cost"]
    assert cost["mw"] == rtb["site"]["project_mw"]
    assert {line["key"] for line in cost["lines"]} == {"application_fee", "advance", "security"}
    assert all(line["source_id"] and line["as_of"] for line in cost["lines"])
    assert rtb["facts"] and all(f["source_id"] for f in rtb["facts"])
    assert rtb["parcel_ids"] and rtb["scored_at"]


def test_rtb_export_follows_the_evidence(seeded: None, ann: TestClient) -> None:  # noqa: F811
    sid = _site_id(ann)
    ann.put(
        f"/api/sites/{sid}/power-evidence", json={"level": "application_filed", "document_date": "2026-09-01"}
    )
    rtb = ann.get(f"/api/sites/{sid}/rtb").json()
    assert rtb["power_evidence"]["level"] == "application_filed"
    power = next(w for w in rtb["workstreams"] if w["key"] == "power")
    assert any("Wniosek złożony" in r["text_pl"] and r["as_of"] == "2026-09-01" for r in power["reasons"])


def test_rtb_of_an_unknown_site_is_404(seeded: None, ann: TestClient) -> None:  # noqa: F811
    assert ann.get("/api/sites/nope/rtb").status_code == 404
