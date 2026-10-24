"""What people paste into Sprawdź działkę: ids, coordinates, Google Maps links, "obręb numer", and mistakes."""

from __future__ import annotations

import pytest

from dclh_pipeline.parcel_input import MAX_INPUTS, parse_inputs

ID = "306201_1.0002.1573"


def test_a_teryt_id_is_recognised() -> None:
    parsed = parse_inputs(ID)
    assert [(i.kind, i.parcel_id) for i in parsed.items] == [("id", ID)]
    assert parsed.issues == []


def test_ids_with_an_ar_sheet_and_a_slash_number() -> None:
    parsed = parse_inputs("146510_8.0004.AR_5.211/3")
    assert parsed.items[0].parcel_id == "146510_8.0004.AR_5.211/3"


def test_several_ids_on_one_line_and_across_lines() -> None:
    parsed = parse_inputs(f"{ID}, 101103_5.0011.1\n141906_2.0001.141/9; {ID}")
    assert [i.parcel_id for i in parsed.items] == [ID, "101103_5.0011.1", "141906_2.0001.141/9"]
    assert parsed.duplicates == 1


def test_a_trailing_sentence_period_is_not_part_of_the_id() -> None:
    assert parse_inputs(f"Działka {ID}.").items[0].parcel_id == ID


@pytest.mark.parametrize(
    "text",
    ["52,2297 20,9511", "52.2297, 20.9511", "52,2297, 20,9511", "52.2297° N, 20.9511° E", "20.9511 52.2297"],
)
def test_coordinates_in_any_common_spelling_and_order(text: str) -> None:
    (item,) = parse_inputs(text).items
    assert item.kind == "point"
    assert item.lat == pytest.approx(52.2297) and item.lon == pytest.approx(20.9511)


def test_coordinates_outside_poland_are_an_issue_not_a_lookup() -> None:
    parsed = parse_inputs("40.7128 -74.0060")
    assert parsed.items == []
    assert "poza Polską" in parsed.issues[0].message_pl


def test_google_maps_links_with_coordinates() -> None:
    at = parse_inputs("https://www.google.com/maps/@52.2297,20.9511,15z")
    place = parse_inputs("https://www.google.com/maps/place/X/@52.0,19.0,17z/data=!3d52.2297!4d20.9511")
    assert at.items[0].lat == pytest.approx(52.2297)
    assert place.items[0].lat == pytest.approx(52.2297)  # the pin (!3d!4d) wins over the viewport (@)


def test_a_short_maps_link_explains_why_it_cannot_be_used() -> None:
    parsed = parse_inputs("https://maps.app.goo.gl/AbCdEf")
    assert parsed.items == []
    assert "skrócone linki" in parsed.issues[0].message_pl


def test_obreb_and_number() -> None:
    (item,) = parse_inputs("Konin 0002 1573").items
    assert (item.kind, item.query) == ("name", "Konin 0002 1573")


def test_a_bare_number_asks_for_the_obreb() -> None:
    parsed = parse_inputs("141/9")
    assert parsed.items == []
    assert parsed.issues[0].text == "141/9" and "Brakuje obrębu" in parsed.issues[0].message_pl


def test_garbage_is_named_back_to_the_user() -> None:
    parsed = parse_inputs("co to jest???")
    assert parsed.issues[0].text == "co to jest???"


def test_mixed_paste_keeps_valid_items_and_reports_the_rest() -> None:
    parsed = parse_inputs(f"{ID}\n52,2297 20,9511\n141/9\n\n  \n")
    assert [i.kind for i in parsed.items] == ["id", "point"]
    assert len(parsed.issues) == 1


def test_empty_input() -> None:
    parsed = parse_inputs("  \n\n")
    assert parsed.items == [] and parsed.issues == []


def test_more_than_the_limit_is_cut_and_counted() -> None:
    ids = "\n".join(f"306201_1.0002.{n}" for n in range(1, MAX_INPUTS + 8))
    parsed = parse_inputs(ids)
    assert len(parsed.items) == MAX_INPUTS and parsed.dropped == 7
