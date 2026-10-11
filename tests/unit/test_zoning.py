"""Planning-zone code mapping. The table is the assumption to check against the APP schema on real data."""

from __future__ import annotations

import pytest

from dclh_pipeline.model.zoning import base_code, zoning_kind_from_code
from dclh_pipeline.scoring.types import ZoningKind


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P", "P"),
        ("PU", "PU"),
        ("PU1", "PU"),
        ("P/U", "P/U"),
        ("p/u.2", "P/U"),
        ("U/P", "U/P"),
        ("P.1", "P"),
        (" p u 3", "PU"),
        ("E", "E"),
        ("IT", "IT"),
        ("SG", "SG"),
        ("U", "U"),
        ("12", None),
        ("", None),
    ],
)
def test_base_code(raw: str, expected: str | None) -> None:
    assert base_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("PU1", ZoningKind.MPZP_PRODUCTION),
        ("P/U", ZoningKind.MPZP_PRODUCTION),
        ("E", ZoningKind.MPZP_PRODUCTION),
        ("IT", ZoningKind.MPZP_PRODUCTION),
        ("SG", ZoningKind.PLAN_OGOLNY_SG),
        ("U", ZoningKind.OTHER),  # services, not production
        ("MN", ZoningKind.OTHER),
        ("ZL", ZoningKind.OTHER),
        ("", ZoningKind.OTHER),
    ],
)
def test_kind_from_code(raw: str, kind: ZoningKind) -> None:
    assert zoning_kind_from_code(raw) is kind


def test_procedure_outranks_the_code() -> None:
    assert zoning_kind_from_code("PU", procedure_in_progress=True) is ZoningKind.PROCEDURE_IN_PROGRESS
