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
        ("110", [110000]),  # a sloppy "110 kV"
        ("400", [400]),  # volts: a low-voltage side, not a 400 kV node
        ("15000;400", [15000, 400]),  # the commonest tag on the Poland extract: a 15 kV station, 400 V side
        ("20000/400", [20000, 400]),
        ("6000;400", [6000, 400]),
        ("15000;800", [15000, 800]),
        ("20000;500", [20000, 500]),  # a bare number next to volts is volts
        ("110;15", [110000, 15000]),  # all bare: kV
        ("400 kV", [400000]),
        ("0.4", [400]),  # 0.4 kV
        ("230", [230]),  # a lone low-voltage level
        ("220000;110000", [220000, 110000]),
        ("400000;220000;110000", [400000, 220000, 110000]),
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
        ("20000/800", SubstationClass.OTHER),
        ("110000;400", SubstationClass.OTHER),
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
        ("Enea Wytwarzanie", None),
        ("Tauron Wytwarzanie", None),
        ("PGE Górnictwo i Energetyka Konwencjonalna", None),
        ("PSE", Dso.PSE),
        ("Tauron", Dso.TAURON),  # the short names OSM mappers actually write
        ("Energa", Dso.ENERGA),
        ("Enea", Dso.ENEA),
        ("enea", Dso.ENEA),
        ("PGE", Dso.PGE),
        ("Stoen Operator", Dso.STOEN),
        ("Tauron Ekoenergia", None),  # generation, not distribution
        ("PGE GiEK S.A.", None),
        ("PGE Energetyka Kolejowa", None),
        ("KGHM Polska Miedź", None),
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


def test_generation_subsidiaries_are_foreign_operators() -> None:
    assert is_foreign_operator("Enea Wytwarzanie")
    assert is_foreign_operator("PGE Górnictwo i Energetyka Konwencjonalna")


@pytest.mark.parametrize(
    ("tag", "cls"),
    [
        ("15000;400", SubstationClass.OTHER),  # was NODE_220_400: 13,086 of these on the real extract
        ("20000;400", SubstationClass.OTHER),
        ("400", SubstationClass.OTHER),
        ("110000;15000", SubstationClass.GPZ_110_SN),
        ("220000;110000", SubstationClass.NODE_220_400),
        ("400000;220000;110000", SubstationClass.NODE_220_400),
    ],
)
def test_low_voltage_sides_do_not_make_a_transmission_node(tag: str, cls: SubstationClass) -> None:
    assert classify_substation(tag).cls is cls
