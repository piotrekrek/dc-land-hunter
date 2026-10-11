"""Cut a small PRG sample for the tests from the latest downloaded PRG snapshot (run on the VPS).

    uv run python scripts/clip_prg_sample.py --out tests/fixtures/prg/konin_region.geojson

The full download (about 100 MB) stays in data/raw; only the clipped GeoJSON is committed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dclh_pipeline.config import load_sources
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.prg import PrgConnector, clip_to_bbox

# Konin and its surroundings in EPSG:2180 (easting, northing), about 25 km square.
KONIN_REGION = (432_000.0, 468_000.0, 466_000.0, 502_000.0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    store = SnapshotStore(get_settings().raw_dir)
    snapshot = store.latest_good("prg") or store.latest("prg")
    if snapshot is None:
        raise SystemExit("no PRG snapshot found; run `dclh fetch prg` first")
    connector = PrgConnector(load_sources().get("prg"))
    table = connector.parse(snapshot)
    sample = clip_to_bbox(table, KONIN_REGION)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sample.to_file(args.out, driver="GeoJSON")
    print(f"wrote {len(sample)} features to {args.out}")
    print(sample.drop(columns="geometry").groupby("level").size().to_string())


if __name__ == "__main__":
    main()
