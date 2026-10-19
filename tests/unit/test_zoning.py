"""Planning-zone code mapping. The table is the assumption to check against the APP schema on real data."""

from __future__ import annotations

import pytest

from dclh_pipeline.model.zoning import (
    base_code,
    mpzp_outline_kind,
    plan_ogolny_kind,
    sampled_mpzp_kind,
    zoning_kind_from_code,
)
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
        ("P-U", "P/U"),
        ("U-P", "U/P"),
        ("3k U-O/US", "U/O"),  # Warsaw (KIMPZP FUN_SYMB): area number, sub-letter, code
        ("2 KDD", "KDD"),
        ("12.MN", "MN"),
        ("1pu", "PU"),  # a lower-case code right after the number is still the code
        ("4P/U", "P/U"),
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
        ("U-P", ZoningKind.MPZP_PRODUCTION),
        ("7 P-U", ZoningKind.MPZP_PRODUCTION),
        ("SG", ZoningKind.OTHER),  # not an MPZP code; the plan ogólny is read by zone type, see below
        ("3k U-O/US", ZoningKind.OTHER),
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


ZONE = "https://www.gov.pl/static/zagospodarowanieprzestrzenne/codelist/RodzajStrefyPlanistycznejKod/"
STATUS = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/"


@pytest.mark.parametrize(
    ("zone", "status", "kind"),
    [
        ("strefaGospodarcza", "legalForce", ZoningKind.PLAN_OGOLNY_SG),
        ("strefaGospodarcza", "elaboration", ZoningKind.PROCEDURE_IN_PROGRESS),
        ("strefaGospodarcza", "adoption", ZoningKind.PROCEDURE_IN_PROGRESS),
        ("strefaWielofunkcyjnaZZabudowaMieszkaniowaJednorodzinna", "legalForce", ZoningKind.OTHER),
        (
            "strefaWielofunkcyjnaZZabudowaMieszkaniowaJednorodzinna",
            "elaboration",
            None,
        ),  # a draft does not bind
        ("strefaGospodarcza", "obsolete", None),
        ("strefaGospodarcza", None, None),
    ],
)
def test_plan_ogolny_kind_reads_the_zone_type_and_status(
    zone: str, status: str | None, kind: ZoningKind | None
) -> None:
    assert plan_ogolny_kind(ZONE + zone, None if status is None else STATUS + status) is kind


def test_mpzp_outline_is_a_procedure_only_while_in_procedure() -> None:
    assert mpzp_outline_kind(STATUS + "elaboration") is ZoningKind.PROCEDURE_IN_PROGRESS
    assert mpzp_outline_kind(STATUS + "adoption") is ZoningKind.PROCEDURE_IN_PROGRESS
    assert mpzp_outline_kind(STATUS + "legalForce") is None  # in force, but the outline has no designation
    assert mpzp_outline_kind(None) is None


@pytest.mark.parametrize(
    ("points", "kind"),
    [
        ([], None),
        ([[], []], None),  # no plan at any point
        ([["2 KDD", "5 PU"], []], ZoningKind.MPZP_PRODUCTION),  # a road next to PU does not hide it
        ([["PU"], ["MN"]], ZoningKind.MPZP_PRODUCTION),  # half the planned points
        ([["PU"], ["MN"], ["R"]], ZoningKind.OTHER),
        ([["3k U-O/US", "2 KDD"]], ZoningKind.OTHER),
    ],
)
def test_sampled_mpzp_kind(points: list[list[str]], kind: ZoningKind | None) -> None:
    assert sampled_mpzp_kind(points) is kind
