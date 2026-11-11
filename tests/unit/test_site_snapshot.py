"""Pure diff logic of `dclh diff-snapshots`, and the CLI wiring on files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.site_snapshot import diff_snapshots


def entry(score: float, built: str = "green", container: str = "yellow") -> dict[str, Any]:
    return {"score": score, "verdicts": {"built": built, "container": container}}


def test_identical_snapshots_have_no_diff() -> None:
    snap = {"s1": entry(70.0)}
    assert diff_snapshots(snap, snap).empty


def test_score_move_is_strictly_above_threshold() -> None:
    a = {"s1": entry(70.0), "s2": entry(70.0)}
    b = {"s1": entry(75.0), "s2": entry(75.1)}
    assert diff_snapshots(a, b).score_moves == {"s2": (70.0, 75.1)}
    assert diff_snapshots(a, b, threshold=2).score_moves.keys() == {"s1", "s2"}


def test_downward_score_move_counts() -> None:
    assert "s1" in diff_snapshots({"s1": entry(80)}, {"s1": entry(60)}).score_moves


def test_verdict_change_in_one_profile_only() -> None:
    d = diff_snapshots({"s1": entry(70)}, {"s1": entry(70, container="red")})
    assert d.verdict_changes == {"s1": {"container": ("yellow", "red")}}
    assert not d.score_moves


def test_missing_verdicts_compare_as_none() -> None:
    d = diff_snapshots({"s1": {"score": 1.0, "verdicts": None}}, {"s1": entry(1.0)})
    assert d.verdict_changes["s1"]["built"] == (None, "green")


def test_sites_in_one_file_only() -> None:
    d = diff_snapshots({"a": entry(1), "c": entry(1)}, {"b": entry(1), "c": entry(1)})
    assert (d.only_a, d.only_b) == (["a"], ["b"])


def test_cli_diff_reads_files(tmp_path: Path) -> None:
    fa, fb = tmp_path / "a.json", tmp_path / "b.json"
    fa.write_text(json.dumps({"sites": {"s1": entry(70)}}))
    fb.write_text(json.dumps({"sites": {"s1": entry(60, built="red")}}))
    result = CliRunner().invoke(cli.app, ["diff-snapshots", str(fa), str(fb)])
    assert result.exit_code == 0
    assert "verdict built: green -> red" in result.output
    assert "score: 70.0 -> 60.0" in result.output


def test_cli_diff_rejects_a_foreign_file(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    assert CliRunner().invoke(cli.app, ["diff-snapshots", str(bad), str(bad)]).exit_code != 0


def test_cli_snapshot_needs_a_selector(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["snapshot-sites", "--out", str(tmp_path / "o.json")])
    assert result.exit_code != 0
