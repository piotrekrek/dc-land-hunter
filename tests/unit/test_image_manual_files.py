"""Every hand-kept file the code reads from the repo must be copied into the Python image (13.11.2026:
station_class.csv was merged but missing in the image, so the Rzepin override never applied)."""

from pathlib import Path

import pytest

from dclh_pipeline.grid_capacity import GPZ_MAPPING, STATION_CLASS_OVERRIDES
from dclh_pipeline.settings import REPO_ROOT
from dclh_pipeline.sources.pse import PLANNED_STATION_COORDS

DOCKERFILE = REPO_ROOT / "infra" / "docker" / "python.Dockerfile"


@pytest.mark.parametrize("path", [GPZ_MAPPING, STATION_CLASS_OVERRIDES, PLANNED_STATION_COORDS])
def test_the_image_copies_the_file(path: Path) -> None:
    rel = path.relative_to(REPO_ROOT).as_posix()
    copies = [ln.split()[1:] for ln in DOCKERFILE.read_text().splitlines() if ln.startswith("COPY ")]
    assert any(rel in args or any(rel.startswith(a.rstrip("/") + "/") for a in args) for args in copies), rel
