"""KIUG soil classes: the GetFeatureInfo answer, the class III estimate and the sample points.

The XML fixtures are real answers saved on 21.10.2026 (powiat grodziski) and one point outside Poland.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pandas as pd
import pytest
from shapely.geometry import box

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.kiug import (
    ERROR,
    NO_PARCEL,
    PARCEL,
    UNPARSED,
    KiugConnector,
    class_iii_share,
    parse_answer,
    soil_iii_facts,
    soil_points,
)

ClientFactory = Callable[..., PoliteClient]
FIXTURES = Path(__file__).parents[1] / "fixtures" / "kiug"
SPEC = SourceSpec(id="kiug", name="KIUG", used_for="t", plan="mvp", cadence="monthly")


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.xml").read_bytes()


def test_a_real_answer_gives_the_parcel_its_area_and_contours() -> None:
    status, attrs = parse_answer(fixture("class_iii"))
    assert status == PARCEL
    assert attrs["Identyfikator działki"] == "140503_2.0011.83/1"
    assert attrs["Pole pow. w ewidencji gruntów (ha)"] == "0.43"
    assert attrs["Oznaczenie konturu"] == "RIIIa,RIIIb"


def test_a_point_without_a_published_parcel_is_no_parcel() -> None:
    assert parse_answer(fixture("outside")) == (NO_PARCEL, {})


def test_an_answer_that_is_not_xml_is_unparsed() -> None:
    assert parse_answer(b"<html>Service unavailable</html") == (UNPARSED, {})


@pytest.mark.parametrize(
    ("contours", "share"),
    [
        ("RIIIa,RIIIb", 1.0),  # real: all class III
        ("Lzr/PsV,RV", 0.0),  # real: class V only
        ("B", 0.0),  # real: built-up land
        ("RIVa,PsIII", 0.5),
        ("S-RIIIa,Br-RIVb", 0.5),  # orchard and built-up on arable land: the class follows the R part
        ("Lzr/PsIII", 1.0),
        ("LsIII,RIVa", 0.0),  # forest soil class III does not count; forest has its own filter
        ("RI,RII,ŁIII", 1 / 3),  # classes I–II are not class III
        ("", 0.0),
    ],
)
def test_class_iii_share_splits_a_parcel_evenly_over_its_contours(contours: str, share: float) -> None:
    assert class_iii_share(contours) == pytest.approx(share)


def _answers(rows: list[tuple[str, str, str | None, float | None, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"key": k, "x": 0.0, "y": 0.0, "status": s, "parcel_id": p, "parcel_ha": ha, "contours": c}
            for k, s, p, ha, c in rows
        ]
    )


def test_site_share_weights_each_distinct_parcel_by_its_area() -> None:
    answers = _answers(
        [
            ("site-a", PARCEL, "p1", 3.0, "RIIIa"),  # 3 ha all class III
            ("site-a", PARCEL, "p1", 3.0, "RIIIa"),  # the same parcel hit twice counts once
            ("site-a", PARCEL, "p2", 1.0, "RIVa,RV"),  # 1 ha, none
            ("site-a", ERROR, None, None, ""),  # a failed point is skipped
            ("site-b", NO_PARCEL, None, None, ""),  # no parcel at any point: no fact
        ]
    )
    facts = soil_iii_facts(answers, as_of=None)
    assert set(facts) == {"site-a"}
    assert facts["site-a"].value == 75.0
    assert facts["site-a"].source_id == "kiug"
    assert facts["site-a"].confidence == "estimated"


def test_points_are_about_one_per_two_hectares_inside_the_site_and_capped() -> None:
    four_ha = box(0, 0, 200, 200)
    assert len(soil_points(four_ha, max_points=10)) == 2
    big = box(0, 0, 800, 400)  # 32 ha would want 16
    points = soil_points(big, max_points=10)
    assert len(points) == 10 and all(big.contains(p) for p in points)
    sliver = box(0, 0, 1_000, 5)  # thinner than the grid step: still one point
    assert len(soil_points(sliver, max_points=10)) == 1


def test_fetch_asks_in_2180_easting_first_and_keeps_a_failed_point(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.url.params)
        if request.url.params["BBOX"].startswith("699950"):
            return httpx.Response(503)
        return httpx.Response(200, content=fixture("class_iii"))

    connector = KiugConnector(SPEC, points=[("site-a", 605_000, 478_000), ("site-a", 700_000, 478_000)])
    answers = connector.parse(connector.fetch(make_client(handler), SnapshotStore(tmp_path)))
    assert seen[0]["SRS"] == "EPSG:2180" and seen[0]["BBOX"] == "604950.0,477950.0,605050.0,478050.0"
    assert seen[0]["LAYERS"] == "klasouzytki" and seen[0]["VERSION"] == "1.1.1"
    assert list(answers["status"]) == [PARCEL, ERROR]
    assert answers.loc[0, "parcel_ha"] == 0.43
    assert soil_iii_facts(answers, as_of=None)["site-a"].value == 100.0
