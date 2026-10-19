"""Energa load-capacity PDF: joining the station lists (Tabela 2.1) with the group values (Tabela 2.3).

The tables below copy the shape pdfplumber returns for the PDF of 31.08.2026 (header rows, branch headings,
cells wrapped with newlines, a group split across pages). The PDF itself is not committed (2.4 MB).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from dclh_pipeline.sources.energa import capacity_table

FIRST_PAGE = "INFORMACJA\nO WARTOŚCI ŁĄCZNEJ DOSTĘPNEJ ODBIORCZEJ MOCY\n(STAN NA DZIEŃ 31.08.2026 ROKU)\n"
GROUPS_HEADER = ["Nr grupy", "Nazwa grupy", "Model sieci na 2026r.", "Zmiany w kolejnych latach\ni uwagi"]
VALUES_HEADER = [
    ["Nr grupy", "Nazwa grupy", "Rok", None, None, None, None, None, None],
    [None, None, "2026", "2027", "2028", "2029", "2030", "2031", "2035"],
]


def _tables(values: list[list[Any]] | None = None) -> list[list[list[Any]]]:
    page_5 = [
        GROUPS_HEADER,
        ["ENERGA-OPERATOR S.A. Oddział Koszalin", None, None, None],
        ["1", "Dunowo", "Białogard, Dunowo, Kalisz\nPomorski, Kołobrzeg 6 Dywizji", "Mielno (2028-2035)"],
        ["2", "Świdwin", "Barwice, Grzmiąca", ""],
    ]
    page_6 = [  # group 2 continues on the next page: no number, no name
        GROUPS_HEADER,
        [None, None, "Połczyn Zdrój", ""],
        ["36", "Kutno 1", "Kutno sekcja 1, Bożepole*", ""],
        ["37", "Kutno 2", "Kutno sekcja 2", ""],
    ]
    abbreviations = [["Skrót", "Nazwa stacji WN"], ["BLG", "Białogard"]]
    group_values = values or [
        ["1", "Dunowo", "40", "220", "220", "220", "220", "230", "250"],
        ["2", "Świdwin", "0", "120", "155", "155", "155", "155", "155"],
        ["36", "Kutno 1", "25", "25", "25", "25", "25", "20", "115"],
        ["37", "Kutno 2", "15", "15", "20", "85", "85", "35", "-"],
    ]
    return [page_5, page_6, abbreviations, [*VALUES_HEADER, *group_values]]


def test_each_station_gets_its_groups_first_year_value() -> None:
    table = capacity_table(FIRST_PAGE, _tables())
    by_name = dict(zip(table["gpz_name"], table["published_mw"], strict=True))
    assert by_name["Białogard"] == 40.0
    assert by_name["Kalisz Pomorski"] == 40.0  # a name wrapped across two lines
    assert by_name["Kołobrzeg 6 Dywizji"] == 40.0
    assert by_name["Połczyn Zdrój"] == 0.0  # the group continued on the next page; 0 MW is a real value
    assert "Mielno" not in by_name  # joins the group only in 2028
    assert set(table["dso"]) == {"energa"}
    assert set(table["as_of"]) == {pd.Timestamp("2026-08-31")}
    note = table.loc[table["gpz_name"] == "Barwice", "note"].iloc[0]
    assert note == "grupa 2 Świdwin: moc wspólna grupy węzłów 110 kV, rok 2026"


def test_two_sections_of_one_station_count_once() -> None:
    table = capacity_table(FIRST_PAGE, _tables())
    kutno = table[table["gpz_name"].str.startswith("Kutno")]
    assert list(kutno["gpz_name"]) == ["Kutno sekcja 1"]  # "sekcja 2" is the same OSM station
    assert list(kutno["group_no"]) == [36]


def test_a_group_without_a_value_fails() -> None:
    values = [["1", "Dunowo", "40", "", "", "", "", "", ""], ["2", "Świdwin", "0", "", "", "", "", "", ""]]
    with pytest.raises(ValueError, match=r"groups \[36, 37\] have stations but no value"):
        capacity_table(FIRST_PAGE, _tables(values))


def test_a_value_that_is_not_a_number_fails() -> None:
    values = [["1", "Dunowo", "b.d.", "", "", "", "", "", ""]]
    with pytest.raises(ValueError, match=r"group 1 has value 'b\.d\.' for 2026"):
        capacity_table(FIRST_PAGE, _tables(values))


def test_missing_date_or_tables_fail() -> None:
    with pytest.raises(ValueError, match="stan na dzień"):
        capacity_table("INFORMACJA", _tables())
    with pytest.raises(ValueError, match="found 0 station groups and 0 group values"):
        capacity_table(FIRST_PAGE, [])
