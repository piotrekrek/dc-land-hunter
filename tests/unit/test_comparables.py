from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from dclh_pipeline.comparables import (
    Transaction,
    asking_price_outlier,
    comparables,
    months_ago,
    percentile,
)
from dclh_pipeline.config import RcnParams, load_scoring

TODAY = date(2026, 11, 12)
AS_OF = datetime(2026, 11, 1, tzinfo=UTC)
X, Y = 205_000.0, 505_000.0


def tx(
    zl_m2: float = 5.0,
    *,
    dx: float = 0.0,
    use: str = "gruntyRolne",
    when: date = date(2026, 1, 15),
    nier: str = "nieruchomoscGruntowaNiezabudowana",
    trans: str = "wolnyRynek",
    share: str = "1/1",
    powiat: str = "0805",
    area_ha: float = 1.0,
    price_zl: float | None = None,
) -> Transaction:
    return Transaction(
        tx_date=when,
        price_zl=zl_m2 * area_ha * 10_000 if price_zl is None else price_zl,
        area_ha=area_ha,
        land_use=use,
        nier_rodzaj=nier,
        trans_type=trans,
        share=share,
        powiat=powiat,
        x=X + dx,
        y=Y,
    )


def run(transactions: list[Transaction]):  # type: ignore[no-untyped-def]
    return comparables(transactions, x=X, y=Y, powiat="0805", today=TODAY, as_of=AS_OF)


@pytest.mark.parametrize(
    "bad",
    [
        tx(nier="nieruchomoscGruntowaZabudowana"),
        tx(nier="nieruchomoscLokalowa"),
        tx(trans="sprzedazPrzetargowa"),
        tx(share="1/2"),
        tx(price_zl=0),
        tx(area_ha=0),
        tx(when=date(2023, 11, 11)),  # just over 36 months
        tx(when=date(2026, 12, 30)),  # dated in the future (seen in the live register)
    ],
)
def test_transactions_that_are_not_comparable_are_ignored(bad: Transaction) -> None:
    result = run([bad])
    assert result.agricultural.count == 0 and result.other.count == 0
    assert result.confidence == "missing"


def test_36_months_include_the_boundary_day() -> None:
    assert run([tx(when=date(2023, 11, 12))]).agricultural.count == 1


def test_statistics_over_five_near_agricultural_transactions() -> None:
    result = run([tx(p, dx=i * 100) for i, p in enumerate([2.0, 4.0, 6.0, 8.0, 10.0])])
    assert not result.widened
    assert result.confidence == "verified"
    stats = result.agricultural
    assert stats.count == 5
    assert stats.median_zl_m2 == pytest.approx(6.0)
    assert stats.p25_zl_m2 == pytest.approx(4.0)
    assert stats.p75_zl_m2 == pytest.approx(8.0)
    assert result.other.count == 0 and result.other.median_zl_m2 is None


def test_groups_are_split_by_land_use() -> None:
    near = [
        tx(5.0),
        tx(7.0),
        tx(9.0),
        tx(100.0, use="gruntyZabudowaneIZurbanizowane"),
        tx(300.0, use="inne"),
    ]
    result = run(near)
    assert result.agricultural.count == 3 and result.agricultural.median_zl_m2 == pytest.approx(7.0)
    assert result.other.count == 2 and result.other.median_zl_m2 == pytest.approx(200.0)


def test_date_range_of_a_group() -> None:
    result = run([tx(when=date(2025, 3, 1)), tx(when=date(2026, 9, 9))] + [tx() for _ in range(3)])
    assert result.agricultural.first_date == date(2025, 3, 1)
    assert result.agricultural.last_date == date(2026, 9, 9)


def test_fewer_than_five_nearby_widens_to_the_powiat() -> None:
    near = [tx(5.0, dx=1_000), tx(6.0, dx=2_000)]
    far = [tx(20.0 + i, dx=50_000 + i) for i in range(4)]  # same powiat, outside 10 km
    elsewhere = [tx(999.0, dx=50_000, powiat="0807")]
    result = run(near + far + elsewhere)
    assert result.widened
    assert result.confidence == "estimated"
    assert result.agricultural.count == 6
    assert result.agricultural.median_zl_m2 == pytest.approx(statistics_median([5, 6, 20, 21, 22, 23]))


def statistics_median(values: list[float]) -> float:
    import statistics

    return statistics.median(values)


def test_exactly_five_nearby_does_not_widen() -> None:
    near = [tx(5.0, dx=i * 100) for i in range(5)]
    far = [tx(50.0, dx=50_000)]
    result = run(near + far)
    assert not result.widened and result.agricultural.count == 5


def test_radius_is_ten_km_euclidean() -> None:
    inside = [tx(5.0, dx=9_999) for _ in range(5)]
    outside = [tx(5.0, dx=10_001, powiat="0807")]
    assert run(inside + outside).agricultural.count == 5


def test_widening_that_finds_nothing_more_stays_unwidened_and_estimated() -> None:
    result = run([tx(5.0), tx(6.0)])
    assert not result.widened
    assert result.confidence == "estimated"  # fewer than five, nowhere more to look
    assert result.agricultural.count == 2


def test_no_transactions_is_missing() -> None:
    result = run([])
    assert result.confidence == "missing"
    assert result.agricultural.count == 0 and result.agricultural.median_zl_m2 is None
    assert result.source_id == "rcn" and result.as_of == AS_OF


def test_never_imported_has_no_as_of_and_is_missing() -> None:
    result = comparables([tx()], x=X, y=Y, powiat="0805", today=TODAY, as_of=None)
    assert result.confidence == "missing"


@pytest.mark.parametrize(
    ("today", "months", "expected"),
    [
        (date(2026, 11, 12), 36, date(2023, 11, 12)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 2, 29)),
        (date(2026, 1, 15), 2, date(2025, 11, 15)),
    ],
)
def test_months_ago(today: date, months: int, expected: date) -> None:
    assert months_ago(today, months) == expected


def test_percentile_interpolates() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([7.0], 0.75) == 7.0


@pytest.mark.parametrize(
    ("asking", "median", "expected"),
    [
        (60.1, 6.0, True),
        (60.0, 6.0, False),  # exactly 10x is not above
        (10.0, 6.0, False),
        (None, 6.0, False),
        (100.0, None, False),
        (100.0, 0.0, False),
    ],
)
def test_asking_price_outlier(asking: float | None, median: float | None, expected: bool) -> None:
    assert asking_price_outlier(asking, median) is expected


def test_outlier_factor_is_a_parameter() -> None:
    assert asking_price_outlier(31.0, 6.0, factor=5.0) is True
    assert asking_price_outlier(31.0, 6.0, factor=10.0) is False


# --- per-group widening and the minimum area (config/scoring.yaml `rcn:`) ---------------------------------


def test_each_group_widens_on_its_own() -> None:
    # Agricultural: 5 in the circle (stays). Other: 1 in the circle, 5 more in the powiat (widens).
    agri = [tx(5.0, dx=i * 100) for i in range(5)]
    other_near = [tx(300.0, use="gruntyZabudowaneIZurbanizowane", area_ha=0.5)]
    other_far = [tx(400.0 + i, use="inne", dx=40_000 + i, area_ha=0.5) for i in range(5)]
    result = run(agri + other_near + other_far)
    assert (result.agricultural.widened, result.agricultural.confidence) == (False, "verified")
    assert (result.other.widened, result.other.confidence) == (True, "estimated")
    assert result.other.count == 6
    assert result.widened and result.confidence == "verified"  # the headline is the agricultural group


def test_agricultural_widens_while_other_stays_near() -> None:
    other = [tx(300.0 + i, use="inne", area_ha=0.5, dx=i * 50) for i in range(5)]
    agri_near = [tx(5.0)]
    agri_far = [tx(7.0, dx=30_000 + i) for i in range(5)]
    result = run(other + agri_near + agri_far)
    assert (result.agricultural.widened, result.agricultural.count, result.agricultural.confidence) == (
        True,
        6,
        "estimated",
    )
    assert (result.other.widened, result.other.confidence) == (False, "verified")
    assert result.confidence == "estimated"


def test_a_widened_agricultural_median_is_what_the_outlier_check_gets() -> None:
    result = run([tx(5.0)] + [tx(7.0, dx=30_000 + i) for i in range(5)])
    assert result.agricultural.widened
    assert asking_price_outlier(80.0, result.agricultural.median_zl_m2) is True  # > 10 x 6,5


@pytest.mark.parametrize(
    ("area_ha", "kept"),
    [(0.49, 0), (0.5, 1), (3.0, 1)],
)
def test_agricultural_sales_under_half_a_hectare_are_left_out(area_ha: float, kept: int) -> None:
    assert run([tx(5.0, area_ha=area_ha)]).agricultural.count == kept


@pytest.mark.parametrize(("area_ha", "kept"), [(0.09, 0), (0.1, 1), (0.3, 1)])
def test_other_sales_under_a_tenth_of_a_hectare_are_left_out(area_ha: float, kept: int) -> None:
    assert run([tx(300.0, use="inne", area_ha=area_ha)]).other.count == kept


def test_thresholds_come_from_the_config_block() -> None:
    cfg = RcnParams(min_area_ha_agricultural=2.0, radius_km=1, months=6, min_count=2)
    recent = date(2026, 9, 1)
    near = [tx(5.0, dx=500, area_ha=2.0, when=recent), tx(6.0, dx=600, area_ha=2.0, when=recent)]
    ignored = [
        tx(5.0, dx=500, area_ha=1.9, when=recent),  # under the minimum area
        tx(5.0, dx=2_000, area_ha=2.0, when=recent),  # outside 1 km
        tx(5.0, dx=500, area_ha=2.0, when=date(2026, 1, 1)),  # older than 6 months
    ]
    result = comparables(near + ignored, x=X, y=Y, powiat="0805", today=TODAY, as_of=AS_OF, cfg=cfg)
    assert result.agricultural.count == 2 and not result.agricultural.widened
    assert result.agricultural.confidence == "verified"  # 2 >= min_count of 2
    assert (result.radius_m, result.months) == (1000, 6)


def test_rcn_block_in_scoring_yaml_has_the_agreed_values() -> None:
    cfg = load_scoring().rcn
    assert (cfg.min_area_ha_agricultural, cfg.min_area_ha_other) == (0.5, 0.1)
    assert (cfg.radius_km, cfg.months, cfg.min_count, cfg.outlier_factor) == (10, 36, 5, 10)
