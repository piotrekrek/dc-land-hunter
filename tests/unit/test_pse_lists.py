"""PSE lists in PSE's own XLSX form: the closed-switchyard list (live sample) and the art. 7 ust. 8l list.

The art. 7 ust. 8l list names applicants (brief 9.6), so the tests check that the name never reaches the
parsed table or the stored snapshot. Its live file is not committed for that reason; the test builds one in
the same layout with a made-up applicant.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from dclh_pipeline.config import load_sources
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources import pse
from dclh_pipeline.sources.manual import PseAvailPoints, PseClosedPoints, PseOdbPoints
from dclh_pipeline.sources.pse import parse_planned_stations, read_pse_avail, read_pse_closed, read_pse_odb

LIVE = Path(__file__).parents[1] / "fixtures/live/source-verification-2026-10-19"
APPLICANT = "Jan Kowalski Inwestycje"

ODB_HEADER = [
    "ID Obiektu",
    "Nazwa Obiektu",
    "Nazwa OSD/OSP\n(Wybierz opcję)",
    "Nazwa podmiotu ubiegającego się \no przyłączenie",
    "Lokalizacja miejsca przyłączenia\n [nazwa stacji] NN/WN",
    "Poziom napięcia \n(Wybierz opcję)",
    "Wiele miejsc przyłączenia/poziomów napięć\n(Wybierz opcję)",
    "Rodzaj\ninstalacji, urządzeń lub sieci\n(Wybierz opcję)\n",
    "Wprowadzana \ndo sieci\n[MW]",
    "Pobierana\nz sieci\n[MW]",
    "ODB\n[MW]",
    "STATUS \nETAPU PROJEKTU\n(Wybierz opcję)",
    "Data złożenia wniosku o określenie warunków przyłączenia\n\n[dd.mm.rrrr]",
    "Data określenia warunków przyłączenia\n\n[dd.mm.rrrr]",
    "Data zawarcia umowy \no przyłączenie\n\n[dd.mm.rrrr]",
]


def _odb_file(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Informacje publikowane zgodnie z Art. 7 ust. 8l ustawy PE\nStan na 31.08.2026"])
    ws.append([])
    ws.append([None] * 8 + ["Moc przyłączeniowa"])
    ws.append(ODB_HEADER)
    ws.append(
        [
            "WWP/1",
            f"ODB {APPLICANT}",
            "PSE S.A.",
            APPLICANT,
            "Mikułowa",
            "110",
            "NIE",
            "ODB",
            "-",
            "100",
            "100",
            "WNIOSEK w weryfikacji",
            "2026-08-13 00:00:00",
            "-",
            "-",
        ]
    )
    ws.append(
        ["-", "MEE Środa", "PSE S.A.", "Inna Sp. z o.o.", "Kromolice", "110", "NIE", "MEE", "129", "129", "-"]
    )
    ws.append(
        [
            "-",
            "DC",
            "PSE S.A.",
            APPLICANT,
            "Plewiska\nPlewiska; Kromolice",
            "220",
            "TAK",
            "ODB",
            "-",
            "300",
            "300",
            "WARUNKI PRZYŁĄCZENIA wydane",
            "2026-01-12 00:00:00",
            "2026-06-01 00:00:00",
            "-",
        ]
    )
    wb.save(path)
    return path


def _spec(source_id: str):  # type: ignore[no-untyped-def]
    return load_sources().get(source_id)


def test_closed_list_reads_only_the_existing_stations() -> None:
    table = read_pse_closed(LIVE / "pse_closed_20260204.xlsx")
    assert table is not None
    assert len(table) == 37  # one row per switchyard; the planned ones ("III.33") below are left out
    assert table["name"].nunique() == 33
    assert not table["name"].str.match(r"I+\.\d+").any()
    assert set(table["as_of"]) == {pd.Timestamp("2026-02-04")}
    assert table.iloc[0][["name", "symbol", "switchyard"]].tolist() == [
        "Aniołów",
        "ANI",
        "Rozdzielnia 220 kV",
    ]


def test_other_files_are_not_taken_for_pse_lists(tmp_path: Path) -> None:
    csv = tmp_path / "closed.csv"
    csv.write_text("name,as_of\nA,2026-10-01\n", encoding="utf-8")
    assert read_pse_closed(csv) is None
    assert read_pse_odb(LIVE / "pse_closed_20260204.xlsx") is None
    assert read_pse_closed(_odb_file(tmp_path / "odb.xlsx")) is None


def test_odb_rows_keep_station_voltage_mw_status_and_dates_only(tmp_path: Path) -> None:
    table = read_pse_odb(_odb_file(tmp_path / "odb.xlsx"))
    assert table is not None
    assert list(table["name"]) == ["Mikułowa", "Plewiska", "Kromolice"]  # the MEE row is not a load
    assert list(table.columns) == [
        "name",
        "as_of",
        "voltage_kv",
        "odb_mw",
        "status",
        "applied_on",
        "conditions_on",
        "agreement_on",
    ]
    first = table.iloc[0]
    assert (first["voltage_kv"], first["odb_mw"], first["applied_on"], first["conditions_on"]) == (
        "110",
        "100",
        "2026-08-13",
        "",
    )
    assert APPLICANT not in table.to_csv()


def test_import_stores_the_reduced_table_never_the_applicant(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "raw")
    source = PseOdbPoints(_spec("pse_odb"), _odb_file(tmp_path / "odb.xlsx"))
    snapshot, table = source.import_file(store)
    assert snapshot.path.suffix == ".csv"
    stored = snapshot.path.read_text(encoding="utf-8")
    assert APPLICANT not in stored
    assert "Mikułowa" in stored
    assert table.geometry.isna().all()  # no coordinates: located by name later
    reparsed = PseOdbPoints(_spec("pse_odb"), snapshot.path).parse(snapshot)  # the stored CSV parses again
    assert list(reparsed["name"]) == list(table["name"])


def test_closed_list_imports_through_the_manual_path(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "raw")
    source = PseClosedPoints(_spec("pse_closed"), LIVE / "pse_closed_20260204.xlsx")
    snapshot, table = source.import_file(store)
    assert len(table) == 37
    assert store.latest_good("pse_closed") == snapshot
    assert (LIVE / "pse_closed_20260204.xlsx").exists()  # the original stays where it was


PLANNED_TEXT = """INFORMACJA
O DOSTĘPNOŚCI MOCY PRZYŁĄCZENIOWEJ
(STAN NA 30 WRZEŚNIA 2026 ROKU)
SPIS TREŚCI
5. ZESTAWIENIE DOSTĘPNYCH MOCY PRZYŁĄCZENIOWYCH
ODBIORCZYCH ....................... 12
5. Zestawienie dostępnych mocy przyłączeniowych odbiorczych
Dostępna moc
L.p. Stacje
przyłączeniowa
1. NOR – Norki 100
3. WYP – stacja w rejonie Warszawy (Wypędy) 200
4. MKA – stacja w rejonie Legnicy 1000
11. ZER – stacja w rejonie Poznania 500
Oprócz wyżej wymienionych stacji elektroenergetycznych PSE S.A. zaplanowały budowę innych.
1. CPK – stacja w rejonie Portu Polska 250
8. PSC – stacja w rejonie Poznania 500
"""


def test_planned_stations_come_from_the_first_table_only() -> None:
    as_of, stations = parse_planned_stations(PLANNED_TEXT)
    assert as_of == date(2026, 9, 30)
    assert stations == [
        ("NOR", "Norki", 100),
        ("WYP", "stacja w rejonie Warszawy (Wypędy)", 200),
        ("MKA", "stacja w rejonie Legnicy", 1000),
        ("ZER", "stacja w rejonie Poznania", 500),
    ]


def test_planned_stations_text_without_the_section_is_refused() -> None:
    with pytest.raises(ValueError, match="section 5"):
        parse_planned_stations("(STAN NA 30 WRZEŚNIA 2026 ROKU)\nnothing here")
    with pytest.raises(ValueError, match="STAN NA"):
        parse_planned_stations("5. Zestawienie dostępnych mocy przyłączeniowych odbiorczych\n")


def test_planned_stations_are_placed_from_the_table_and_the_rest_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(pse, "_pdf_text", lambda _path: PLANNED_TEXT)
    pdf = tmp_path / "avail.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    table = read_pse_avail(pdf)
    assert table is not None
    assert list(table["symbol"]) == ["MKA", "ZER"]  # NOR and WYP have no row in the committed table
    assert table["odb_mw"].tolist() == ["1000", "500"]
    assert "NOR, WYP" in caplog.text
    assert read_pse_avail(tmp_path / "list.xlsx") is None  # not a PDF: some other format's job


def test_planned_stations_import_stores_a_csv_that_parses_to_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pse, "_pdf_text", lambda _path: PLANNED_TEXT)
    pdf = tmp_path / "avail.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    spec = next(s for s in load_sources().sources if s.id == "pse_avail")
    store = SnapshotStore(tmp_path / "raw")
    snapshot, table = PseAvailPoints(spec, pdf).import_file(store)
    assert snapshot.path.suffix == ".csv"  # the PDF itself is not kept
    assert len(table) == 2 and table.geometry.notna().all()
    again = PseAvailPoints(spec, snapshot.path).parse(snapshot)  # what the scoring run reads back
    assert list(again["name"]) == list(table["name"])
    zer = again[again["name"].str.startswith("ZER")].geometry.iloc[0]
    assert 350_000 < zer.x < 400_000 and 480_000 < zer.y < 520_000  # Poznań in EPSG:2180
