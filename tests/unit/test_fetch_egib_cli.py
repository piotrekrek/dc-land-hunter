"""`dclh fetch-egib` end to end: the real Konin sample is served by a mock transport; no network."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import Settings
from tests.conftest import FIXTURES

KONIN_SAMPLE = FIXTURES / "live" / "cge-konin" / "egib_getfeature.gml"


def serve_sample(request: httpx.Request) -> httpx.Response:
    if "bbox" not in request.url.params:
        return httpx.Response(404)  # robots.txt
    start = int(request.url.params["startIndex"])
    if start == 0:
        return httpx.Response(200, content=KONIN_SAMPLE.read_bytes())
    head = (
        KONIN_SAMPLE.read_bytes()
        .split(b"<wfs:member>")[0]
        .replace(b'numberReturned="20"', b'numberReturned="0"')
    )
    return httpx.Response(200, content=head + b"</wfs:FeatureCollection>\n")


def test_fetch_egib_parses_the_konin_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path, contact_email="test@example.org")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "PoliteClient",
        lambda **kwargs: PoliteClient(
            transport=httpx.MockTransport(serve_sample),
            sleep=lambda _: None,
            **kwargs,  # type: ignore[arg-type]
        ),
    )
    result = CliRunner().invoke(
        cli.app, ["fetch-egib", "--lon", "18.2514", "--lat", "52.2360", "--radius-m", "300"]
    )
    assert result.exit_code == 0, result.output
    assert "parsed 20 parcels" in result.output
    assert "306201_1." in result.output
    assert list((tmp_path / "raw" / "egib" / "objects").rglob("*.json"))  # the bundle is stored
