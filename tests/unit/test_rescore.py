"""rescore-telecom: the fibre node distance is backfilled and moves the stored score by its own effect only."""

from __future__ import annotations

import json
from typing import Any

import pytest
from shapely.geometry import Point, box

from dclh_pipeline.config import ScoringConfig, load_profiles, load_scoring
from dclh_pipeline.model.features import GeomIndex, Layer, SiteLayers, build_site_features
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.rescore import patch_site, with_telecom_thresholds
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.run import to_jsonable
from dclh_pipeline.sources.uke import DATA_AS_OF

CFG: ScoringConfig = load_scoring()
PROFILES = load_profiles()
GEOM = box(0, 0, 400, 250)  # 10 ha
NODE_1_KM = Layer("uke", GeomIndex([Point(1400, 100)]), DATA_AS_OF, Confidence.ESTIMATED)


def _stored() -> tuple[dict[str, Any], dict[str, Any]]:
    """A site as an old search stored it: no node distance, and a score from older rules (+3 on grid_node)."""
    site = Site("s", None, ("p",), GEOM, 10.0, 0.6, 60.0, needs_split_suggestion=False)
    features = build_site_features(site, SiteLayers(), CFG)
    score = to_jsonable(score_site(features, CFG))
    score["score"] += 3.0
    score["upside_score"] += 3.0
    score["items"][0]["points"] += 3.0
    return json.loads(json.dumps(to_jsonable(features))), score


def test_only_the_telecom_effect_is_added_to_the_stored_score() -> None:
    features, score = _stored()
    patched = patch_site(features, score, GEOM, NODE_1_KM, CFG, PROFILES)
    new_score = json.loads(patched["score_json"])
    telecom = next(i for i in new_score["items"] if i["key"] == "conn_telecom")
    assert telecom["confidence"] == "estimated" and telecom["source_id"] == "uke"
    assert telecom["points"] == telecom["max_points"] > 0  # 1 km: inside the full-points band
    assert patched["score"] == pytest.approx(score["score"] + telecom["points"])
    assert patched["completeness"] > score["completeness"]
    # The older rules' points elsewhere are kept, not silently recomputed.
    assert new_score["items"][0] == score["items"][0]
    assert [i["key"] for i in new_score["items"]] == [i["key"] for i in score["items"]]
    conn = next(c for c in new_score["categories"] if c["category"] == "connectivity")
    old_conn = next(c for c in score["categories"] if c["category"] == "connectivity")
    assert conn["points"] == pytest.approx(old_conn["points"] + telecom["points"])
    assert json.loads(patched["features_json"])["dist_telecom_node_km"]["value"] == pytest.approx(1.0)


def test_running_it_again_changes_nothing() -> None:
    features, score = _stored()
    once = patch_site(features, score, GEOM, NODE_1_KM, CFG, PROFILES)
    twice = patch_site(
        json.loads(once["features_json"]), json.loads(once["score_json"]), GEOM, NODE_1_KM, CFG, PROFILES
    )
    assert twice["score"] == pytest.approx(once["score"])
    assert twice["completeness"] == pytest.approx(once["completeness"])
    a, b = json.loads(once["score_json"]), json.loads(twice["score_json"])
    assert a["items"] == b["items"] and a["categories"] == b["categories"]
    assert twice["features_json"] == once["features_json"]


NODE_5_KM = Layer("uke", GeomIndex([Point(5400, 100)]), DATA_AS_OF, Confidence.ESTIMATED)


def _points(patched: dict[str, Any]) -> float:
    return next(i["points"] for i in json.loads(patched["score_json"])["items"] if i["key"] == "conn_telecom")


def test_a_threshold_change_moves_the_score_by_the_telecom_difference_only() -> None:
    old_cfg = with_telecom_thresholds(CFG, 2, 15)
    features, score = _stored()
    on_old = patch_site(
        features, score, GEOM, NODE_5_KM, old_cfg, PROFILES
    )  # backfilled by the old thresholds
    stored_features, stored_score = json.loads(on_old["features_json"]), json.loads(on_old["score_json"])
    moved = patch_site(stored_features, stored_score, GEOM, NODE_5_KM, CFG, PROFILES, previous=old_cfg)
    assert _points(moved) < _points(on_old)  # 5 km: fewer points under 1 / 8 km than under 2 / 15 km
    assert moved["score"] == pytest.approx(on_old["score"] + _points(moved) - _points(on_old))
    assert (
        json.loads(moved["score_json"])["items"][0] == stored_score["items"][0]
    )  # older grid_node points kept
    # A second run with the same `previous` finds the new points already stored and changes nothing.
    again = patch_site(
        json.loads(moved["features_json"]),
        json.loads(moved["score_json"]),
        GEOM,
        NODE_5_KM,
        CFG,
        PROFILES,
        previous=old_cfg,
    )
    assert again["score"] == pytest.approx(moved["score"])
    a, b = json.loads(again["score_json"]), json.loads(moved["score_json"])
    assert a["items"] == b["items"]
    for x, y in zip(a["categories"], b["categories"], strict=True):
        assert x["points"] == pytest.approx(y["points"])
        assert x["upside_points"] == pytest.approx(y["upside_points"])


def test_without_previous_a_threshold_change_is_not_applied() -> None:
    """Without `previous` the stored telecom points stay in the score; the guard is the explicit flag."""
    old_cfg = with_telecom_thresholds(CFG, 2, 15)
    features, score = _stored()
    on_old = patch_site(features, score, GEOM, NODE_5_KM, old_cfg, PROFILES)
    plain = patch_site(
        json.loads(on_old["features_json"]), json.loads(on_old["score_json"]), GEOM, NODE_5_KM, CFG, PROFILES
    )
    assert plain["score"] == pytest.approx(on_old["score"])
