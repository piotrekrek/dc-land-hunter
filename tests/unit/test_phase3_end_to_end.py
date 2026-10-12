"""QA of the whole Phase 3 chain as an owner would run it: OSM file → substations → DSO and PSE files → matching →
site features → score. Everything is generated in a temp folder; no network and no database."""

from __future__ import annotations

from pathlib import Path

import osmium
from osmium.osm.mutable import Node
from shapely.geometry import box

from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.model.capacity import build_gpz_capacity_layer, build_point_layer
from dclh_pipeline.model.features import SiteLayers, build_grid_index, build_site_features
from dclh_pipeline.model.gpz_match import gpz_candidates, match_summary, station_points
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.types import GridBadge
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.manual import EneaCapacity, PseClosedPoints, load_gpz_mapping
from dclh_pipeline.sources.osm_power import OsmPowerConnector, to_grid

CFG = load_scoring()


def _spec(source_id: str):  # type: ignore[no-untyped-def]
    return next(s for s in load_sources().sources if s.id == source_id)


def test_owner_workflow_end_to_end(tmp_path: Path) -> None:
    # 1. OSM extract → substations with names and operators
    pbf = tmp_path / "mini.osm.pbf"
    with osmium.SimpleWriter(str(pbf)) as w:
        w.add_node(
            Node(
                id=1,
                location=(16.90, 52.40),
                tags={
                    "power": "substation",
                    "name": "GPZ Piaski",
                    "voltage": "110000;15000",
                    "operator": "Enea Operator",
                },
            )
        )
        w.add_node(
            Node(id=2, location=(16.99, 52.46), tags={"power": "substation", "operator": "Enea Operator"})
        )
    store = SnapshotStore(tmp_path / "raw")
    osm = OsmPowerConnector(_spec("osm_power"), min_substations=2, min_available_mb=0)
    osm_snapshot = store.save("osm_power", pbf.read_bytes(), suffix=".osm.pbf")
    substations, lines = to_grid(osm.parse(osm_snapshot))
    piaski = next(s for s in substations if s.name == "GPZ Piaski")

    # 2. the owner's DSO file (a name that differs from OSM) and mapping file (for the unnamed station)
    dso_csv = tmp_path / "enea.csv"
    dso_csv.write_text(
        "dso,gpz_name,published_mw,as_of,note\n"
        "enea,Stacja elektroenergetyczna Piaski,12.5,2026-09-01,\n"
        "enea,Żabno Nowe,3,2026-09-01,mapowane ręcznie\n"
        "enea,Nieistniejąca,4,2026-09-01,\n",
        encoding="utf-8",
    )
    unnamed = next(s for s in substations if s.name is None)
    mapping = tmp_path / "gpz_mapping.csv"
    mapping.write_text(
        f"osm_id,dso,dso_gpz_name,note\nn{unnamed.osm_id},enea,Żabno Nowe,\n", encoding="utf-8"
    )
    _, capacity = EneaCapacity(_spec("enea"), dso_csv).import_file(store)

    layer, results = build_gpz_capacity_layer(
        capacity,
        gpz_candidates(substations),
        manual=load_gpz_mapping(mapping),
        stations=station_points(substations),
        min_score=CFG.grid.gpz_match_min_score,
    )
    summary = match_summary(results)
    assert (summary["matched"], summary["manual"], summary["total"]) == (
        2,
        1,
        3,
    )  # the third is reported unmatched
    unmatched = [r for r in results if r.candidate_key is None]
    assert [r.dso_gpz_name for r in unmatched] == ["Nieistniejąca"]
    assert "najlepsza zgodność" in unmatched[0].reason  # the miss is reported with its reason, not dropped

    # 3. a PSE closed-switchyard list one kilometre from the site
    site_centre = piaski.geom.buffer(1).centroid
    closed_csv = tmp_path / "closed.csv"
    closed_csv.write_text(
        f"name,as_of,x_2180,y_2180\nRozdzielnia A,2026-10-01,{site_centre.x + 1000},{site_centre.y}\n",
        encoding="utf-8",
    )
    _, closed = PseClosedPoints(_spec("pse_closed"), closed_csv).import_file(store)

    # 4. features and score for a site next to the GPZ
    geom = box(site_centre.x - 150, site_centre.y - 150, site_centre.x + 150, site_centre.y + 150)
    site = Site("site-1", None, ("p1",), geom, geom.area / 10_000, 0.7, geom.area / 10_000 * 6, False)
    layers = SiteLayers(
        grid=build_grid_index(substations, lines, source_id="osm_power"),
        capacity=layer,
        pse_closed=build_point_layer(closed, "pse_closed"),
    )
    features = build_site_features(site, layers, CFG)
    score = score_site(features, CFG)

    assert features.published_gpz_headroom_mw is not None
    assert features.published_gpz_headroom_mw.value == 12.5
    assert features.published_gpz_headroom_mw.source_id == "enea"
    assert features.published_gpz_headroom_mw.confidence is Confidence.ESTIMATED  # fuzzy match
    assert features.closed_switchyard_nearby is not None and features.closed_switchyard_nearby.value is True
    assert features.powered_brownfield is None  # no industrial layer loaded: missing, not False
    assert score.grid_badge is GridBadge.PUBLISHED
    by_key = {item.key: item for item in score.items}
    headroom = by_key["grid_headroom"]
    assert headroom.points > 0
    assert (
        headroom.points == headroom.max_points
    )  # full credit; points are scaled to the grid category weight
    assert headroom.source_id == "enea"
    assert by_key["grid_closed"].points < 0  # the closed switchyard costs points
