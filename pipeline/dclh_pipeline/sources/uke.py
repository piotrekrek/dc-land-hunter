"""Telecom nodes (proxy for fibre access): SIIS nodes with a fibre interface, for `conn_telecom`.

UKE's live services are closed to us (checked from the VPS on 20.11.2026, docs/SOURCES.md): the PIT GeoServer
answers 404 on every documented path and the SIIS API host times out. dane.gov.pl says SIIS was switched off
and its data set will not be updated. What remains is the SIIS archive on dane.gov.pl (data set 588, CC0),
whose newest file holds the inventory as of 31.12.2021: a RAR5 of CSV views.

From that archive this connector keeps the nodes (`infrastructure_node_view.csv`) that have at least one fibre
interface (`infrastructure_nodeinterface_view.csv`, `transmission_medium = światłowodowe`) and coordinates.
Radio-only and copper-only nodes are left out: almost every site has one within 2 km, so they say nothing
about fibre. SIIS records the links between nodes, not the cable routes, so the distance to a node is an
estimate of fibre access, never a promise of capacity.

The operator's name is kept only for firms with a legal form (sp. z o.o., S.A. and so on). Many operators are
sole traders, whose name is a person's name; for those only the SIIS entity id is kept. The archive is read
with `bsdtar` (libarchive-tools, in the Python image): Python has no RAR5 reader.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import GeomIndex, Layer
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://api.dane.gov.pl/media/resources/20221018/2021.rar"
NODES_MEMBER = "2021/2021/infrastructure_node_view.csv"
INTERFACES_MEMBER = "2021/2021/infrastructure_nodeinterface_view.csv"
# The archive's data date (dane.gov.pl resource 42009, `data_date`): the values are this old whenever fetched.
DATA_AS_OF = datetime(2021, 12, 31, tzinfo=UTC)
FIBRE = "światłowodowe"
COLUMNS = ["node_id", "operator_id", "operator", "shared", "fibre_tech", "lon", "lat"]
# Poland's extent with a small margin (WGS84). A node outside it is a data error, not a node.
LON_RANGE = (14.0, 24.2)
LAT_RANGE = (48.9, 54.9)
MIN_NODES = 50_000  # the 2021 archive holds about 97,500; far fewer means a broken file
_LEGAL_FORM = re.compile(r"sp\.\s*z\s*o\.\s*o|spółk|\bS\.\s*A\b|sp\.\s*[kjp]\b|\bs\.\s*c\b", re.IGNORECASE)
_TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


class UkeConnector(Source):
    id: ClassVar[str] = "uke"

    def __init__(self, spec: SourceSpec, *, min_nodes: int = MIN_NODES) -> None:
        super().__init__(spec)
        self.min_nodes = min_nodes

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        tmp_root = store.root / self.id / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            archive = Path(tmp) / "siis-2021.rar"
            headers = client.download(ARCHIVE_URL, archive)
            expected = headers.get("content-length")
            if expected is not None and archive.stat().st_size != int(expected):
                size = archive.stat().st_size
                raise ValueError(f"uke: archive is {size} bytes, server announced {expected}")
            nodes, interfaces = Path(tmp) / "nodes.csv", Path(tmp) / "interfaces.csv"
            extract_member(archive, NODES_MEMBER, nodes)
            extract_member(archive, INTERFACES_MEMBER, interfaces)
            content = reduce_nodes(nodes, interfaces, min_nodes=self.min_nodes)
        return store.save(
            self.id, content, suffix=".csv", url=ARCHIVE_URL, content_type="text/csv", compress=True
        )

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """The fibre nodes, with `x`, `y` in EPSG:2180. Raises on an empty file or a node outside Poland."""
        raw = snapshot.path.read_bytes()
        text = gzip.decompress(raw) if snapshot.path.suffix == ".gz" else raw
        dtypes = {"node_id": str, "operator_id": str}
        table = pd.read_csv(io.BytesIO(text), dtype=dtypes, keep_default_na=False)
        if list(table.columns) != COLUMNS:
            raise ValueError(f"uke: unexpected snapshot columns {list(table.columns)}")
        if table.empty:
            raise ValueError("uke: the saved snapshot has no nodes")
        _check_in_poland(table["lon"], table["lat"])
        x, y = _TO_2180.transform(table["lon"].to_numpy(), table["lat"].to_numpy())
        return table.assign(x=x, y=y)


def extract_member(archive: Path, member: str, dest: Path) -> None:
    """Write one file of the RAR archive to `dest` with bsdtar (streams; the node views are 70-90 MB)."""
    with dest.open("wb") as out:
        done = subprocess.run(["bsdtar", "-xOf", str(archive), member], stdout=out, stderr=subprocess.PIPE)
    if done.returncode != 0 or dest.stat().st_size == 0:
        raise ValueError(f"uke: could not extract {member}: {done.stderr.decode(errors='replace').strip()}")


def reduce_nodes(nodes_csv: Path, interfaces_csv: Path, *, min_nodes: int = MIN_NODES) -> bytes:
    """The fibre nodes with coordinates as CSV bytes (`COLUMNS`), sorted by node id.

    Raises when fewer than `min_nodes` remain or a coordinate falls outside Poland.
    """
    fibre_tech: dict[str, set[str]] = defaultdict(set)
    with interfaces_csv.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["transmission_medium"] == FIBRE:
                fibre_tech[row["node_id"]].add(row["technology_optic_fiber"] or "")
    rows: list[list[str]] = []
    no_coords = 0
    with nodes_csv.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["id"] not in fibre_tech:
                continue
            if not row["latitude"] or not row["longitude"]:
                no_coords += 1
                continue
            lon, lat = float(row["longitude"]), float(row["latitude"])
            _check_in_poland(pd.Series([lon]), pd.Series([lat]), node=row["id"])
            name = row["users_entity_id_name"].strip()
            rows.append(
                [
                    row["id"],
                    row["users_entity_id"],
                    name if _LEGAL_FORM.search(name) else "",
                    "1" if "współdzielony" in row["owner_type"] else "0",
                    "|".join(sorted(t for t in fibre_tech[row["id"]] if t)),
                    f"{lon:.6f}",
                    f"{lat:.6f}",
                ]
            )
    if no_coords:
        log.info("uke: %d fibre nodes have no coordinates in SIIS; left out", no_coords)
    if len(rows) < min_nodes:
        raise ValueError(
            f"uke: only {len(rows)} fibre nodes with coordinates (expected {min_nodes}+); not saving"
        )
    rows.sort(key=lambda r: int(r[0]))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _check_in_poland(lon: pd.Series, lat: pd.Series, *, node: str | None = None) -> None:
    bad = ~(lon.between(*LON_RANGE) & lat.between(*LAT_RANGE))
    if bad.any():
        where = f"node {node}" if node else f"{int(bad.sum())} nodes"
        raise ValueError(f"uke: {where} outside Poland ({lon[bad].iloc[0]}, {lat[bad].iloc[0]})")


def telecom_node_layer(store: SnapshotStore, spec: SourceSpec) -> Layer | None:
    """The SIIS fibre nodes as a point layer, or None when no good snapshot exists.

    `as_of` is the archive's data date (31.12.2021), not the download date: the site card must not make
    five-year-old data look current.
    """
    snapshot = store.latest_good("uke")
    if snapshot is None:
        return None
    table = UkeConnector(spec).parse(snapshot)
    return Layer(
        source_id="uke",
        index=GeomIndex(Point(x, y) for x, y in zip(table["x"], table["y"], strict=True)),
        as_of=DATA_AS_OF,
        confidence=Confidence.ESTIMATED,
    )
