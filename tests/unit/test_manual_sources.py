"""Manual imports for the Phase 3 grid sources: validation, row numbers, snapshots and the mapping file."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pyproj import Transformer

from dclh_pipeline.config import load_sources
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.manual import (
    EneaCapacity,
    PseClosedPoints,
    load_gpz_mapping,
    read_table,
)

CAPACITY_HEADER = "dso,gpz_name,published_mw,as_of,note\n"
POINTS_HEADER = "name,as_of,x_2180,y_2180\n"


def _spec(source_id: str) -> Any:
    return next(s for s in load_sources().sources if s.id == source_id)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _enea(path: Path) -> EneaCapacity:
    return EneaCapacity(_spec("enea"), path)


def test_valid_capacity_file_parses_blank_mw_as_not_published(tmp_path: Path) -> None:
    file = _write(
        tmp_path / "enea.csv",
        CAPACITY_HEADER
        + 'enea,GPZ Piaski,"12,5",2026-09-01,\n'
        + "enea,Żabno,,2026-09-01,nie publikuje\n"
        + "enea,Ostrów,0,2026-09-01,\n",
    )
    source = _enea(file)
    table = source.parse(source.store_file(_store(tmp_path)))
    assert list(table["gpz_name"]) == ["GPZ Piaski", "Żabno", "Ostrów"]
    assert table["published_mw"].iloc[0] == pytest.approx(12.5)
    assert math.isnan(table["published_mw"].iloc[1])
    assert table["published_mw"].iloc[2] == 0.0  # a published zero is a real value
    assert set(table["dso"]) == {"enea"}
    assert table["as_of"].iloc[0] == pd.Timestamp("2026-09-01")


def test_semicolon_separated_file_is_read(tmp_path: Path) -> None:
    file = _write(tmp_path / "enea.csv", "dso;gpz_name;published_mw;as_of\nenea;Piaski;7;2026-09-01\n")
    source = _enea(file)
    table = source.parse(source.store_file(_store(tmp_path)))
    assert table["published_mw"].iloc[0] == 7.0


def test_xlsx_file_is_read_like_csv(tmp_path: Path) -> None:
    file = tmp_path / "enea.xlsx"
    pd.DataFrame(
        {"DSO": ["enea"], "GPZ name": ["Piaski"], "Published MW": [9.0], "as_of": ["2026-09-01"]}
    ).to_excel(file, index=False)
    source = _enea(file)
    table = source.parse(source.store_file(_store(tmp_path)))
    assert table["gpz_name"].iloc[0] == "Piaski"
    assert table["published_mw"].iloc[0] == 9.0


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("tauron,Piaski,5,2026-09-01,", "row 2: dso is 'tauron'"),
        ("enea,,5,2026-09-01,", "row 2: gpz_name is empty"),
        ("enea,Piaski,-1,2026-09-01,", "row 2: published_mw -1.0 must be"),
        ("enea,Piaski,abc,2026-09-01,", "row 2: published_mw 'abc' is not a number"),
        ("enea,Piaski,5,01.09.2026,", "row 2: as_of '01.09.2026' is not a date"),
        ("enea,Piaski,5,,", "row 2: as_of is empty"),
    ],
)
def test_bad_rows_fail_the_whole_file_with_the_row_number(tmp_path: Path, row: str, message: str) -> None:
    file = _write(tmp_path / "enea.csv", CAPACITY_HEADER + row + "\n")
    source = _enea(file)
    with pytest.raises(ValueError, match=re.escape(message)):
        source.parse(source.store_file(_store(tmp_path)))


def test_duplicate_gpz_name_is_rejected(tmp_path: Path) -> None:
    file = _write(
        tmp_path / "enea.csv",
        CAPACITY_HEADER + "enea,Piaski,1,2026-09-01,\nenea,Piaski,2,2026-09-01,\n",
    )
    source = _enea(file)
    with pytest.raises(ValueError, match="listed twice"):
        source.parse(source.store_file(_store(tmp_path)))


def test_missing_columns_are_named_in_the_error(tmp_path: Path) -> None:
    file = _write(tmp_path / "enea.csv", "dso,gpz_name\nenea,Piaski\n")
    source = _enea(file)
    with pytest.raises(ValueError, match="missing columns"):
        source.parse(source.store_file(_store(tmp_path)))


def test_fetch_stores_the_file_as_a_snapshot(tmp_path: Path) -> None:
    file = _write(tmp_path / "enea.csv", CAPACITY_HEADER + "enea,Piaski,1,2026-09-01,\n")
    store = _store(tmp_path)
    snapshot = _enea(file).store_file(store)
    assert snapshot.source_id == "enea"
    assert snapshot.url == "manual:enea.csv"
    assert snapshot.path.read_bytes() == file.read_bytes()
    assert file.exists()  # the owner's original stays where it was


def test_points_from_xy_are_kept_in_2180(tmp_path: Path) -> None:
    file = _write(tmp_path / "closed.csv", POINTS_HEADER + "Rozdzielnia A,2026-10-01,400000,500000\n")
    source = PseClosedPoints(_spec("pse_closed"), file)
    table = source.parse(source.store_file(_store(tmp_path)))
    assert table["name"].iloc[0] == "Rozdzielnia A"
    assert table.crs.to_epsg() == 2180
    assert (table.geometry.iloc[0].x, table.geometry.iloc[0].y) == (400_000.0, 500_000.0)


def test_points_from_lat_lon_are_transformed_to_2180(tmp_path: Path) -> None:
    file = _write(tmp_path / "closed.csv", "name,as_of,lat,lon\nPoznań,2026-10-01,52.4083,16.9341\n")
    source = PseClosedPoints(_spec("pse_closed"), file)
    table = source.parse(source.store_file(_store(tmp_path)))
    point = table.geometry.iloc[0]
    expected = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True).transform(16.9341, 52.4083)
    assert (point.x, point.y) == pytest.approx(expected)
    # Poznań is at about (360 km, 505 km) in EPSG:2180, the same reference as the PRG axis check
    assert abs(point.x - 360_000) < 15_000
    assert abs(point.y - 505_000) < 15_000


def test_points_outside_poland_are_rejected(tmp_path: Path) -> None:
    file = _write(tmp_path / "closed.csv", POINTS_HEADER + "Daleko,2026-10-01,20000,20000\n")
    source = PseClosedPoints(_spec("pse_closed"), file)
    with pytest.raises(ValueError, match=r"row 2: point .* outside Poland"):
        source.parse(source.store_file(_store(tmp_path)))


def test_points_need_coordinates(tmp_path: Path) -> None:
    file = _write(tmp_path / "closed.csv", "name,as_of\nA,2026-10-01\n")
    source = PseClosedPoints(_spec("pse_closed"), file)
    with pytest.raises(ValueError, match="needs x_2180 \\+ y_2180, or lat \\+ lon"):
        source.parse(source.store_file(_store(tmp_path)))


def test_gpz_mapping_skips_comments_and_blank_rows(tmp_path: Path) -> None:
    file = _write(
        tmp_path / "gpz_mapping.csv",
        "# comment line\nosm_id,dso,dso_gpz_name,note\n,,,\n17,enea,GPZ Piaski,ręcznie\n",
    )
    assert load_gpz_mapping(file) == {"enea": {"piaski": 17}}


def test_gpz_mapping_rejects_duplicates_and_bad_ids(tmp_path: Path) -> None:
    dup = _write(
        tmp_path / "dup.csv",
        "osm_id,dso,dso_gpz_name,note\n1,enea,Piaski,\n2,enea,GPZ Piaski,\n",
    )
    with pytest.raises(ValueError, match="mapped twice"):
        load_gpz_mapping(dup)
    bad = _write(tmp_path / "bad.csv", "osm_id,dso,dso_gpz_name,note\nabc,enea,Piaski,\n")
    with pytest.raises(ValueError, match="not an integer"):
        load_gpz_mapping(bad)


def test_committed_mapping_file_is_valid() -> None:
    path = Path(__file__).parents[2] / "data" / "manual" / "gpz_mapping.csv"
    assert load_gpz_mapping(path) == {}  # the template is blank until the Phase 3 matching is reviewed


def test_read_table_normalises_headers(tmp_path: Path) -> None:
    file = _write(tmp_path / "t.csv", " GPZ Name ,Published MW\n Piaski , 3 \n")
    table = read_table(file)
    assert list(table.columns) == ["gpz_name", "published_mw"]
    assert table.iloc[0].tolist() == ["Piaski", "3"]


def _store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "raw")
