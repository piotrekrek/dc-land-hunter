"""The site card's feature map must survive non-scalar facts such as deposit_list (prod 500, 12.11.2026)."""

from dclh_api.routers.sites import _fact_map


def _fact(value: object) -> dict[str, object]:
    return {"value": value, "source_id": "midas", "as_of": "2026-11-12T00:00:00Z", "confidence": "verified"}


def test_list_valued_fact_is_left_out_scalars_kept() -> None:
    out = _fact_map(
        {
            "deposit_overlap_pct": _fact(63.0),
            "deposit_list": _fact([{"name": "Rzepin", "status": "R", "mineral": "WĘGLE BRUNATNE"}]),
            "empty_list": _fact([]),
            "missing": None,
        }
    )
    assert set(out) == {"deposit_overlap_pct"}
    assert out["deposit_overlap_pct"].value == 63.0
