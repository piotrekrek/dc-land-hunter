from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dclh_pipeline.model.grid import (
    Dso,
    SubstationClass,
    classify_substation,
    derive_operator,
    is_foreign_operator,
    parse_voltages,
)
from dclh_pipeline.model.provenance import Confidence


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("110000;15000", [110000, 15000]),
        ("400000", [400000]),
        ("110 kV", [110000]),
        ("110", [110000]),  # bare kV value
        ("15000;110000;15000", [110000, 15000]),
        ("", []),
        (None, []),
        ("yes", []),
    ],
)
def test_parse_voltages(tag: str | None, expected: list[int]) -> None:
    assert parse_voltages(tag) == expected


@pytest.mark.parametrize(
    ("tag", "cls"),
    [
        ("110000;15000", SubstationClass.GPZ_110_SN),
        ("110000;20000;6000", SubstationClass.GPZ_110_SN),
        ("400000;220000;110000", SubstationClass.NODE_220_400),
        ("220000", SubstationClass.NODE_220_400),
        ("110000", SubstationClass.OTHER),  # 110 kV only: a switchyard, not a GPZ
        ("15000", SubstationClass.OTHER),
        (None, SubstationClass.UNKNOWN_VOLTAGE),
        ("n/a", SubstationClass.UNKNOWN_VOLTAGE),
    ],
)
def test_classify(tag: str | None, cls: SubstationClass) -> None:
    assert classify_substation(tag).cls is cls


def test_unknown_voltage_is_kept_at_low_confidence() -> None:
    c = classify_substation(None)
    assert c.cls is SubstationClass.UNKNOWN_VOLTAGE and c.confidence is Confidence.MISSING


@given(st.lists(st.integers(min_value=1_000, max_value=800_000), min_size=1, max_size=4))
def test_any_valid_voltage_classifies_and_220_plus_is_a_node(volts: list[int]) -> None:
    c = classify_substation(";".join(str(v) for v in volts))
    assert c.cls is not SubstationClass.UNKNOWN_VOLTAGE
    assert (c.cls is SubstationClass.NODE_220_400) == (max(volts) >= 220_000)


@pytest.mark.parametrize(
    ("operator", "dso"),
    [
        ("Enea Operator", Dso.ENEA),
        ("Energa-Operator", Dso.ENERGA),
        ("ENERGA Operator SA", Dso.ENERGA),
        ("Tauron Dystrybucja", Dso.TAURON),
        ("PGE Dystrybucja", Dso.PGE),
        ("PSE", Dso.PSE),
        ("Polskie Sieci Elektroenergetyczne", Dso.PSE),
        ("ArcelorMittal Poland", None),
        (None, None),
    ],
)
def test_operator(operator: str | None, dso: Dso | None) -> None:
    assert derive_operator(operator) is dso


def test_foreign_operator_flags_powered_brownfield_candidates() -> None:
    assert is_foreign_operator("ArcelorMittal Poland")
    assert not is_foreign_operator("Tauron Dystrybucja")
    assert not is_foreign_operator(None)
    assert not is_foreign_operator("  ")
