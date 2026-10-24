"""ULDK requests and answers. The CGE Konin sample is real; the multi-record and error answers are synthetic."""

from __future__ import annotations

from pathlib import Path

import pytest

from dclh_pipeline.sources.uldk import (
    by_id_params,
    by_name_params,
    by_xy_params,
    parse_point,
    parse_records,
    wgs84_to_puwg,
)

KONIN = Path(__file__).parents[1] / "fixtures" / "live" / "cge-konin" / "uldk_getparcelbyxy.txt"


def test_real_answer_has_the_parcel_and_its_polygon() -> None:
    (parcel,) = parse_records(KONIN.read_text(encoding="utf-8"))
    assert parcel.parcel_id == "306201_1.0002.1573"
    assert parcel.wkt is not None and parcel.wkt.startswith("SRID=2180;POLYGON")


def test_several_records_come_back_in_order() -> None:
    text = "0\n111111_1.0001.1|woj|powiat A|gmina A|SRID=2180;POLYGON((0 0,1 0,1 1,0 0))\n222222_1.0002.2|woj|powiat B|gmina B\n"
    parcels = parse_records(text)
    assert [p.parcel_id for p in parcels] == ["111111_1.0001.1", "222222_1.0002.2"]
    assert parcels[1].wkt is None  # geometry not requested or missing


def test_point_parser_still_returns_the_first_record() -> None:
    assert parse_point(KONIN.read_text(encoding="utf-8")).commune == "Konin (miasto)"


@pytest.mark.parametrize("text", ["", "\n", "-1 brak wyników", "-1\nbrak wyników\n", "1\nzły status"])
def test_error_or_empty_answers_raise_with_uldk_text(text: str) -> None:
    with pytest.raises(ValueError, match="ULDK"):
        parse_records(text)


def test_status_ok_without_a_record_is_an_error() -> None:
    with pytest.raises(ValueError, match="brak rekordu"):
        parse_records("0\n")


def test_a_record_with_too_few_fields_is_an_error() -> None:
    with pytest.raises(ValueError, match="format"):
        parse_records("0\n306201_1.0002.1573|wielkopolskie\n")


def test_request_params() -> None:
    assert by_xy_params(448894.6, 485814.39)["xy"] == "448894.60,485814.39,2180"
    assert by_id_params("306201_1.0002.1573") == {
        "request": "GetParcelById",
        "id": "306201_1.0002.1573",
        "result": "teryt,voivodeship,county,commune,geom_wkt",
        "srid": "2180",
    }
    assert by_name_params("Konin 0002 1573")["request"] == "GetParcelByIdOrNr"


def test_wgs84_goes_to_puwg_easting_first() -> None:
    # CGE Konin (capture.json): lon 18.2514, lat 52.236 -> x 448894.60, y 485814.39 in the ULDK request.
    x, y = wgs84_to_puwg(52.236, 18.2514)
    assert x == pytest.approx(448894.6, abs=60) and y == pytest.approx(485814.4, abs=60)
