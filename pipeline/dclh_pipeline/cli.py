"""`dclh` command-line interface."""

from __future__ import annotations

import json
import logging
import os
import resource
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from dclh_pipeline import __version__, capture, followup, parcel_spike
from dclh_pipeline.config import load_aoi_config, load_scoring, load_sources
from dclh_pipeline.discovery import STATUS_ICON, discover, render_markdown, save_report, update_sources_doc
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.response_cache import ResponseCache
from dclh_pipeline.settings import REPO_ROOT, get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.energa import EnergaConnector
from dclh_pipeline.sources.gdos import GdosConnector
from dclh_pipeline.sources.heat import HeatConnector
from dclh_pipeline.sources.manual import MANUAL_CAPACITY, MANUAL_POINTS
from dclh_pipeline.sources.osm_power import OsmPowerConnector, summarize_grid
from dclh_pipeline.sources.prg import PrgConnector
from dclh_pipeline.sources.psi import PsiConnector
from dclh_pipeline.sources.tauron import TauronConnector

app = typer.Typer(help="DC Land Hunter pipeline", no_args_is_help=True)
console = Console()

# Connectors that `dclh fetch` can run without the database.
CONNECTORS: dict[str, type[Source]] = {
    "prg": PrgConnector,
    "osm_power": OsmPowerConnector,
    "gdos": GdosConnector,
    "tauron": TauronConnector,
    "energa": EnergaConnector,
    "heat": HeatConnector,
    "psi": PsiConnector,
}


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def version() -> None:
    """Print the pipeline version."""
    console.print(__version__)


@app.command("config-check")
def config_check() -> None:
    """Validate config/scoring.yaml and config/sources.yaml."""
    scoring = load_scoring()
    sources = load_sources()
    console.print(f"scoring.yaml OK: weights {scoring.weights.model_dump()}")
    plans = {p: len(sources.by_plan(p)) for p in ("mvp", "v2", "manual")}
    console.print(f"sources.yaml OK: {len(sources.sources)} sources {plans}")


@app.command("sources")
def list_sources() -> None:
    """List configured sources."""
    table = Table("id", "plan", "cadence", "core", "probes", "name")
    for s in load_sources().sources:
        table.add_row(s.id, s.plan, s.cadence, "yes" if s.core else "", str(len(s.probes)), s.name)
    console.print(table)


@app.command("discover")
def discover_cmd(
    source: Annotated[list[str] | None, typer.Option("--source", "-s", help="Only these source ids")] = None,
    capture_network: Annotated[
        bool, typer.Option(help="Open interactive maps in headless Chromium and record their data requests")
    ] = False,
    out: Annotated[Path, typer.Option(help="Report directory")] = REPO_ROOT / "data" / "discovery",
    write_docs: Annotated[bool, typer.Option(help="Update the generated table in docs/SOURCES.md")] = False,
    ran_from: Annotated[
        str, typer.Option(help="Where this runs, e.g. 'VPS Warszawa' (goes into the report)")
    ] = "unknown",
) -> None:
    """Phase 0 discovery spike: probe every MVP source and write a report."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        console.print("[yellow]DCLH_CONTACT_EMAIL is not set; requests will carry a placeholder contact.[/]")
    config = load_sources()
    specs = [s for s in config.sources if not source or s.id in source]
    unknown = set(source or []) - {s.id for s in specs}
    if unknown:
        raise typer.BadParameter(f"unknown source ids: {sorted(unknown)}")
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,  # discovery reports failures; it doesn't need to push through them
    ) as client:
        report = discover(specs, client, capture_network=capture_network, ran_from=ran_from)
    json_path, md_path = save_report(report, out)
    for s in report.sources:
        console.print(f"{STATUS_ICON[s.status]} {s.source_id:<14} {s.status.value}")
    console.print(f"Report: {json_path} and {md_path}")
    if write_docs:
        update_sources_doc(REPO_ROOT / "docs" / "SOURCES.md", render_markdown(report))
        console.print("docs/SOURCES.md updated")


@app.command("fetch")
def fetch_cmd(
    source: Annotated[str, typer.Argument(help="Source id, e.g. prg")],
    reuse: Annotated[
        bool,
        typer.Option("--reuse", help="Parse the latest saved snapshot instead of downloading again"),
    ] = False,
) -> None:
    """Fetch and parse one source now, without the database. Saves the snapshot under data/raw."""
    if source not in CONNECTORS:
        raise typer.BadParameter(f"no connector for {source!r}; available: {sorted(CONNECTORS)}")
    settings = get_settings()
    config = load_sources()
    spec = next(s for s in config.sources if s.id == source)
    connector = CONNECTORS[source](spec)
    store = SnapshotStore(settings.raw_dir)
    if reuse:
        saved = store.latest(source)
        if saved is None:
            raise typer.BadParameter(f"no saved snapshot of {source!r} to reuse; run without --reuse first")
        snapshot = saved
    else:
        with PoliteClient(
            user_agent=settings.user_agent,
            rate_limit_per_s=config.defaults.rate_limit_per_s,
            timeout_s=config.defaults.timeout_s,
            retries=2,
        ) as client:
            snapshot = connector.fetch(client, store)
    console.print(f"snapshot {snapshot.sha256[:12]} · {snapshot.size_bytes} bytes · {snapshot.path}")
    table = connector.parse(snapshot)
    store.mark_good(snapshot)  # parse worked, so this snapshot can serve as the fallback later
    # ru_maxrss is in kilobytes on Linux
    console.print(
        f"parsed {len(table)} rows · columns {list(table.columns)} · "
        f"peak memory {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB"
    )
    group = next((col for col in ("level", "kind") if col in table.columns), None)
    if group:
        console.print(table.drop(columns="geometry").groupby(group).size().to_string())
    if source == "osm_power":
        for label, count in summarize_grid(table):
            console.print(f"{label:<60} {count:>8}")


@app.command("import-manual")
def import_manual_cmd(
    source: Annotated[str, typer.Argument(help="enea, energa, tauron, pse_closed, pse_odb or pse_avail")],
    file: Annotated[Path, typer.Argument(help="CSV or XLSX with the canonical columns (sources/manual.py)")],
) -> None:
    """Import a published file for a source with no verified endpoint.

    Validates every row, then stores a snapshot. A rejected file is not stored, and the file itself is left
    where it is.
    """
    manual = {**MANUAL_CAPACITY, **MANUAL_POINTS}
    if source not in manual:
        raise typer.BadParameter(f"{source!r} has no manual import; available: {sorted(manual)}")
    if not file.is_file():
        raise typer.BadParameter(f"no such file: {file}")
    spec = next(s for s in load_sources().sources if s.id == source)
    connector = manual[source](spec, file)
    store = SnapshotStore(get_settings().raw_dir)
    try:
        snapshot, table = connector.import_file(store)
    except ValueError as exc:
        raise typer.BadParameter(f"{file.name} was not imported: {exc}") from exc
    console.print(f"snapshot {snapshot.sha256[:12]} · {snapshot.size_bytes} bytes · {snapshot.path}")
    console.print(f"validated {len(table)} rows · columns {list(table.columns)}")


@app.command("gpz-report")
def gpz_report_cmd(
    out: Annotated[Path | None, typer.Option(help="CSV with every row (default: <data dir>/reports)")] = None,
) -> None:
    """Match the saved DSO capacity lists and PSE station lists to OSM and print the match rate per source.

    Uses the newest good snapshot of each source and of `osm_power` (parsing it takes a few minutes).
    The CSV lists every name with its OSM station or the reason it stayed unmatched.
    """
    import csv

    from dclh_pipeline.config import load_scoring
    from dclh_pipeline.grid_capacity import load_grid_capacity
    from dclh_pipeline.model.gpz_match import match_summary
    from dclh_pipeline.sources.osm_power import to_grid

    settings = get_settings()
    config = load_sources()
    store = SnapshotStore(settings.raw_dir)
    osm_snapshot = store.latest_good("osm_power") or store.latest("osm_power")
    if osm_snapshot is None:
        raise typer.BadParameter("no saved snapshot of 'osm_power'; run `dclh fetch osm_power` first")
    substations, _ = to_grid(OsmPowerConnector(config.get("osm_power")).parse(osm_snapshot))
    loaded = load_grid_capacity(store, config, substations, load_scoring())
    if not loaded.results:
        raise typer.BadParameter("no DSO or PSE snapshot to match; fetch or import one first")
    names = {s.osm_id: s.name or "" for s in substations}
    for source_id in sorted({r.dso for r in loaded.results}):
        summary = match_summary([r for r in loaded.results if r.dso == source_id])
        snapshot = loaded.snapshots[source_id]
        console.print(
            f"{source_id:<11} {summary['matched']:>4} / {summary['total']:<4} matched "
            f"({summary['rate']:.0%}; fuzzy {summary['fuzzy']}, manual {summary['manual']}, "
            f"published location {summary['published']}) · snapshot {snapshot.fetched_at:%d.%m.%Y}"
        )
    target = out or settings.data_dir / "reports" / f"gpz_match_{datetime.now(UTC):%Y-%m-%d}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "name", "method", "score", "osm_id", "osm_name", "reason"])
        for r in loaded.results:
            key = r.candidate_key
            writer.writerow(
                [
                    r.dso,
                    r.dso_gpz_name,
                    r.method,
                    f"{r.score:.0f}",
                    key or "",
                    names.get(key, "") if key else "",
                    r.reason,
                ]
            )
    console.print(f"Report: {target}")


@app.command("discover-followup")
def discover_followup_cmd(
    out: Annotated[Path | None, typer.Option(help="Report directory (default: <data dir>/discovery)")] = None,
) -> None:
    """Ask the open questions for bdot10k, kimpzp, isok and ru, with a verdict for each. Run from Poland."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,
    ) as client:
        report = followup.run(client)
    console.print(report)  # first: a failed save must never lose the report
    folder = out or settings.data_dir / "discovery"
    target = folder / f"followup-{datetime.now(UTC):%Y-%m-%dT%H%M}.md"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
    except OSError as exc:
        console.print(f"Report could not be saved to {target}: {exc}. Copy it from the output above.")
        return
    console.print(f"Saved: {target}")


@app.command("capture-sample")
def capture_sample_cmd(
    name: Annotated[str, typer.Option(help="Place name, used for the output folder")],
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    radius_m: Annotated[float, typer.Option(help="Half-width of the square search area, metres")] = 300,
    out: Annotated[
        Path | None, typer.Option(help="Output folder (default: tests/fixtures/live/<name>)")
    ] = None,
) -> None:
    """Record a small live sample from EGiB (WFS) and ULDK for one place, for fixtures. Run from Poland."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    egib_spec = config.get("egib")
    egib_url = next(str(p.url) for p in egib_spec.probes if getattr(p, "kind", None) == "ogc")
    place = capture.Place(name=name, lon=lon, lat=lat, radius_m=radius_m)
    target = out or capture.default_out_dir(REPO_ROOT, place)
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,
    ) as client:
        meta = capture.capture(client, egib_url=egib_url, place=place, out_dir=target)
    for r in meta["responses"]:
        console.print(f"{r['source_id']:<6} HTTP {r['status']}  {r['bytes']} B  -> {target / r['file']}")
    console.print(f"Metadata: {target / 'capture.json'}")


@app.command("fetch-egib")
def fetch_egib_cmd(
    lon: Annotated[float, typer.Option(help="Centre longitude (WGS84)")],
    lat: Annotated[float, typer.Option(help="Centre latitude (WGS84)")],
    radius_m: Annotated[float, typer.Option(help="Half-width of the square search area, metres")] = 300,
) -> None:
    """Fetch and parse EGiB parcels around one point, without the database. Run from Poland."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    spec = config.get("egib")
    place = capture.Place(name="egib-fetch", lon=lon, lat=lat, radius_m=radius_m)
    connector = EgibConnector(spec, bboxes=[place.bbox])
    store = SnapshotStore(settings.raw_dir)
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:
        snapshot = connector.fetch(client, store)
    console.print(f"snapshot {snapshot.sha256[:12]} · {snapshot.size_bytes} bytes · {snapshot.path}")
    table = connector.parse(snapshot)
    console.print(f"parsed {len(table)} parcels · columns {list(table.columns)}")
    if len(table):
        console.print(table["parcel_id"].head(5).to_string(index=False))


@app.command("search")
def search_cmd(
    powiat: Annotated[str, typer.Argument(help="Powiat TERYT code, e.g. 1421 (pruszkowski)")],
    write: Annotated[
        bool, typer.Option("--write", help="Replace this powiat's sites with this run (needs the database)")
    ] = False,
) -> None:
    """Search one powiat: grid seed, EGiB parcels, clusters, scores. Run from Poland.

    Needs saved snapshots of `prg` and `osm_power` (`dclh fetch prg`, `dclh fetch osm_power`). Parcels are
    downloaded now. Prints the funnel counts and timings so a first run also measures the cost (R7).
    The nightly `plan_searches` job runs the same search for several powiats.
    """
    from dclh_pipeline.db import session_scope
    from dclh_pipeline.powiat_search import record_attempt, search_powiat

    try:
        found = search_powiat(powiat, get_settings())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"powiat {found.teryt} {found.name}")
    for label, value in found.result.report.lines():
        console.print(f"{label:<44} {value:>10}")
    console.print(
        f"peak memory {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB"
        "  (layers still missing: soil I–II, heritage)"
    )
    top = sorted(found.result.scored, key=lambda s: -s.score.score)[:10]
    for item in top:
        console.print(
            f"{item.site.site_id} {item.site.area_ha:6.1f} ha {item.score.score:5.1f} pkt"
            f"{' WYKLUCZONA' if item.score.excluded else ''}"
        )
    if write:
        from datetime import UTC, datetime

        from dclh_pipeline.scoring.run import write_sites

        now = datetime.now(UTC)
        with session_scope() as session:
            count = write_sites(session, found.result.scored, scored_at=found.scored_at, powiat=found.teryt)
            record_attempt(
                session, found.teryt, ok=True, at=now, sites=count, seconds=round(found.seconds, 1)
            )
        console.print(f"wrote {count} sites for powiat {found.teryt}")


@app.command("kw-probe")
def kw_probe_cmd(
    sites: Annotated[int, typer.Option(help="Top sites by score (not excluded) to take parcels from")] = 50,
    max_parcels: Annotated[int, typer.Option(help="Stop after this many parcels (about 3 s each)")] = 300,
    powiat: Annotated[str | None, typer.Option(help="Only sites of this powiat, e.g. 1421")] = None,
    out: Annotated[Path | None, typer.Option(help="Report directory (default: <data dir>/discovery)")] = None,
) -> None:
    """Measure how many parcels of the scored sites show a KW number in public EGiB services. Run from Poland.

    Reads parcel ids from the `sites` table, asks ULDK for a point in each parcel and KIEG GetFeatureInfo
    at that point. Writes nothing to the database; saves a Markdown report and a CSV (parcel, site, status,
    KW).
    """
    import csv

    from sqlalchemy import text

    from dclh_pipeline import kw_probe
    from dclh_pipeline.db import session_scope

    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    query = "SELECT site_id, parcel_ids FROM sites WHERE NOT excluded"
    params: dict[str, object] = {"n": sites}
    if powiat:
        query += " AND powiat = :powiat"
        params["powiat"] = powiat
    with session_scope() as session:
        rows = session.execute(text(query + " ORDER BY score DESC LIMIT :n"), params).all()
    todo = [(pid, site_id) for site_id, parcel_ids in rows for pid in parcel_ids][:max_parcels]
    if not todo:
        raise typer.BadParameter("no sites for this selection; run `dclh search <powiat> --write` first")
    console.print(f"{len(rows)} sites, probing {len(todo)} parcels")
    config = load_sources()
    results = []
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,
    ) as client:
        for i, (pid, site_id) in enumerate(todo, 1):
            result = kw_probe.probe_parcel(client, pid, site_id)
            results.append(result)
            console.print(f"{i:>4}/{len(todo)} {pid:<28} {result.status:<18} {result.kw or ''}")
    now = datetime.now(UTC)
    report = kw_probe.render_report(results, sites=len(rows), stamp=f"{now:%Y-%m-%d %H:%M UTC}")
    console.print(report)  # first: a failed save must never lose the report
    folder = out or settings.data_dir / "discovery"
    stem = folder / f"kw-probe-{now:%Y-%m-%dT%H%M}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".md").write_text(report, encoding="utf-8")
        with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["parcel_id", "site_id", "status", "kw", "note"])
            writer.writerows((r.parcel_id, r.site_id, r.status, r.kw or "", r.note) for r in results)
    except OSError as exc:
        console.print(f"Report could not be saved to {stem}: {exc}. Copy it from the output above.")
        return
    console.print(f"Saved: {stem}.md and {stem}.csv")


@app.command("load-grid")
def load_grid_cmd() -> None:
    """Load the saved OSM grid into the database, so the map shows stations and lines.

    Parses the latest `osm_power` snapshot (no download; run `dclh fetch osm_power` first) and replaces the
    `substations` and `lines` tables in one transaction. Needs the database.
    """
    from dclh_pipeline.db import session_scope
    from dclh_pipeline.grid_load import load_grid

    settings = get_settings()
    config = load_sources()
    snapshot = SnapshotStore(settings.raw_dir).latest("osm_power")
    if snapshot is None:
        raise typer.BadParameter("no saved snapshot of 'osm_power'; run `dclh fetch osm_power` first")
    table = OsmPowerConnector(config.get("osm_power")).parse(snapshot)
    with session_scope() as session:
        report = load_grid(session, table, as_of=snapshot.fetched_at)
    for cls, count in sorted(report.substations.items()):
        console.print(f"substations {cls:<16} {count:>8}")
    console.print(f"lines >= 110 kV{'':<13} {report.lines:>8}")
    console.print(
        f"not loaded: {report.skipped_substations} other substations, "
        f"{report.skipped_lines} lines below 110 kV or without voltage"
    )


@app.command("load-admin")
def load_admin_cmd() -> None:
    """Load the saved PRG boundaries (voivodeships, powiats, gminas) into `admin_units`.

    Parses the latest `prg` snapshot (no download; run `dclh fetch prg` first) and replaces the table in one
    transaction. Needs the database.
    """
    from dclh_pipeline.admin_units import load_admin_units
    from dclh_pipeline.db import session_scope

    settings = get_settings()
    snapshot = SnapshotStore(settings.raw_dir).latest("prg")
    if snapshot is None:
        raise typer.BadParameter("no saved snapshot of 'prg'; run `dclh fetch prg` first")
    prg = PrgConnector(load_sources().get("prg")).parse(snapshot)
    with session_scope() as session:
        written = load_admin_units(session, prg)
    for level, count in written.items():
        console.print(f"{level:<12} {count:>6}")
    console.print(f"PRG snapshot of {snapshot.fetched_at:%Y-%m-%d}")


@app.command("grid-counts")
def grid_counts_cmd() -> None:
    """Count the loaded substations per voivodeship (Phase 1 plausibility check).

    Needs `dclh load-grid` and `dclh load-admin` to have run. Read-only.
    """
    from dclh_pipeline.admin_units import substation_counts
    from dclh_pipeline.db import session_scope

    with session_scope() as session:
        try:
            counts = substation_counts(session)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    classes = ("GPZ_110_SN", "NODE_220_400", "UNKNOWN_VOLTAGE")
    dsos = sorted({d for c in counts for d in c.by_dso})
    table = Table("TERYT", "voivodeship", *classes, *(f"GPZ {d}" for d in dsos))
    for c in counts:
        table.add_row(
            c.teryt or "",
            c.name,
            *(str(c.by_class[k]) for k in classes),
            *(str(c.by_dso[d]) for d in dsos),
        )
    table.add_row(
        "",
        "total",
        *(str(sum(c.by_class[k] for c in counts)) for k in classes),
        *(str(sum(c.by_dso[d] for c in counts)) for d in dsos),
    )
    console.print(table)


aoi_app = typer.Typer(help="Area of interest (brief 2.1)", no_args_is_help=True)
app.add_typer(aoi_app, name="aoi")


@aoi_app.command("build")
def aoi_build_cmd() -> None:
    """Build the AOI from the saved PRG and OSM snapshots, write data/aoi/aoi.geojson and load the database.

    Needs the saved `prg` and `osm_power` snapshots (`dclh fetch`). Units listed in config/aoi.yaml are added
    after the Vistula split. Prints the share of each voivodeship inside the AOI, as a plausibility check.
    Needs the database.
    """
    from dclh_pipeline import aoi_build
    from dclh_pipeline.db import session_scope
    from dclh_pipeline.sources.prg import PrgConnector

    settings = get_settings()
    config = load_sources()
    store = SnapshotStore(settings.raw_dir)
    prg_snapshot = store.latest("prg")
    osm_snapshot = store.latest("osm_power")
    if prg_snapshot is None or osm_snapshot is None:
        raise typer.BadParameter(
            "need saved 'prg' and 'osm_power' snapshots; run `dclh fetch` for both first"
        )
    # Check the output folder first: the build below takes minutes, and a path the app user cannot write used
    # to fail only after it.
    out = aoi_build.geojson_path(settings.data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    prg = PrgConnector(config.get("prg")).parse(prg_snapshot)
    vistula = aoi_build.read_vistula(osm_snapshot.path)
    included = load_aoi_config().include
    include_geoms = aoi_build.included_geometries(prg, included)
    aoi = aoi_build.build(aoi_build.country_geometry(prg), vistula, include_geoms)
    aoi_build.write_geojson(aoi, out)
    note = f"PRG {prg_snapshot.fetched_at:%Y-%m-%d}, OSM {osm_snapshot.fetched_at:%Y-%m-%d}"
    if included:
        note += ", include " + " ".join(u.teryt for u in included)
    with session_scope() as session:
        aoi_build.load_aoi(session, aoi, note=note)
    console.print(f"AOI {aoi.area / 1e6:,.0f} km² · written to {out}")
    for unit, geom in zip(included, include_geoms, strict=True):
        console.print(f"added {unit.level} {unit.teryt} {unit.name}: {geom.area / 1e6:,.1f} km²")
    table = Table("voivodeship", "inside AOI")
    for name, share in aoi_build.voivodeship_shares(aoi, prg):
        table.add_row(name, f"{share:.0%}")
    console.print(table)


@app.command("enqueue")
def enqueue_cmd(
    kind: str,
    payload: Annotated[str, typer.Option(help="JSON payload")] = "{}",
) -> None:
    """Add a job to the queue (used by the scheduler's crontab)."""
    from dclh_pipeline.db import session_scope
    from dclh_pipeline.jobs import enqueue

    with session_scope() as session:
        job = enqueue(session, kind, json.loads(payload), requested_by="scheduler")
        console.print(f"queued job {job.id} ({kind})")


@app.command("check")
def check_cmd(
    inputs: Annotated[
        list[str],
        typer.Argument(help="Parcel ids, coordinates or a Google Maps link, e.g. 306201_1.0002.1573"),
    ],
) -> None:
    """Check parcels with the layers and scorer of the nightly search. Run from Poland. No database writes."""
    from dclh_pipeline.layers import cached_static_layers
    from dclh_pipeline.parcel_check import CheckContext, check_all, resolve_parcels
    from dclh_pipeline.parcel_input import parse_inputs
    from dclh_pipeline.powiat_search import SEARCH_CACHE_TTL

    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config, cfg = load_sources(), load_scoring()
    store = SnapshotStore(settings.raw_dir)
    aoi = _load_aoi_or_none()
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
        cache=ResponseCache(settings.data_dir / "http-cache", SEARCH_CACHE_TTL),
    ) as client:
        resolution = resolve_parcels(client, parse_inputs("\n".join(inputs)), aoi)
        for issue in resolution.issues:
            console.print(f"[red]{issue.text}[/red]: {issue.message_pl}")
        if not resolution.items:
            raise typer.Exit(1)
        static, cached = cached_static_layers(settings, config, cfg, store)
        console.print(f"warstwy statyczne {'z pamięci' if cached else 'wczytane'}")
        ctx = CheckContext(config=config, cfg=cfg, store=store, client=client, static=static)
        for result in check_all(resolution.items, ctx, _ConsoleCheckReporter()):
            _print_check(result)


@app.command("worker")
def worker_cmd(
    once: Annotated[bool, typer.Option(help="Process pending jobs, then exit")] = False,
    interval: Annotated[float, typer.Option(help="Seconds between polls")] = 10.0,
    kinds: Annotated[
        str | None, typer.Option(help="Only take these job kinds (comma list), e.g. check_parcel")
    ] = None,
    exclude_kinds: Annotated[str | None, typer.Option(help="Never take these job kinds (comma list)")] = None,
    exit_after_idle: Annotated[
        float | None,
        typer.Option(help="Exit after this many idle seconds once a job has run (the container restarts)"),
    ] = None,
) -> None:
    """Poll the jobs table and execute jobs one at a time. Several workers can split the queue by kind."""
    from dclh_pipeline.check_job import KIND as CHECK_KIND
    from dclh_pipeline.check_job import release_when_idle
    from dclh_pipeline.jobs import work

    def names(text: str | None) -> list[str] | None:
        return [k.strip() for k in text.split(",") if k.strip()] if text else None

    only = names(kinds)
    executed = work(
        once=once,
        interval_s=interval,
        kinds=only,
        exclude=names(exclude_kinds) or (),
        # The check worker keeps the static layers between checks but gives them back when it has been idle.
        on_idle=release_when_idle if only and CHECK_KIND in only else None,
        exit_after_idle_s=exit_after_idle,
    )
    console.print(f"executed {executed} job(s)")


user_app = typer.Typer(help="Manage users (there is no self-signup)", no_args_is_help=True)
app.add_typer(user_app, name="user")


def _read_password(password_env: str | None) -> str:
    if password_env:
        value = os.environ.get(password_env)
        if not value:
            raise typer.BadParameter(f"environment variable {password_env} is empty or not set")
        return value
    first = typer.prompt("Password", hide_input=True)
    if first != typer.prompt("Repeat password", hide_input=True):
        raise typer.BadParameter("passwords do not match")
    return str(first)


@user_app.command("create")
def user_create(
    email: str,
    role: Annotated[str, typer.Option(help="admin or member")] = "member",
    password_env: Annotated[
        str | None, typer.Option(help="Read the password from this environment variable (non-interactive)")
    ] = None,
) -> None:
    """Create a user."""
    from dclh_pipeline.auth import PasswordTooShortError, UserExistsError, create_user
    from dclh_pipeline.db import session_scope

    if role not in ("admin", "member"):
        raise typer.BadParameter("role must be admin or member")
    password = _read_password(password_env)
    try:
        with session_scope() as session:
            user = create_user(session, email, password, role=role)  # type: ignore[arg-type]
            console.print(f"created {user.role} {user.email}")
    except (PasswordTooShortError, UserExistsError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@user_app.command("set-password")
def user_set_password(
    email: str,
    password_env: Annotated[
        str | None, typer.Option(help="Read the password from this environment variable")
    ] = None,
) -> None:
    """Change a password (signs the user out everywhere)."""
    from dclh_pipeline.auth import PasswordTooShortError, set_password
    from dclh_pipeline.db import session_scope

    password = _read_password(password_env)
    try:
        with session_scope() as session:
            set_password(session, email, password)
    except (PasswordTooShortError, LookupError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print("password changed")


@user_app.command("list")
def user_list() -> None:
    """List users."""
    from sqlalchemy import select

    from dclh_pipeline.db import session_scope
    from dclh_pipeline.db.models import UserRow

    table = Table("email", "role", "created")
    with session_scope() as session:
        for u in session.scalars(select(UserRow).order_by(UserRow.email)):
            table.add_row(u.email, u.role, u.created_at.strftime("%d.%m.%Y"))
    console.print(table)


if __name__ == "__main__":
    app()


@app.command("check-spike")
def check_spike_cmd(
    parcel_id: Annotated[str, typer.Argument(help="Parcel id, for example 306201_1.0002.1573")],
    name_query: Annotated[
        str | None, typer.Option("--name", help='Also try the unverified name search, e.g. "Konin 0002 1573"')
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Write the timings as JSON to this file")] = None,
) -> None:
    """Time every Sprawdź działkę layer for one parcel, cold and warm. Run from Poland. No database writes."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    store = SnapshotStore(settings.data_dir / "spike-raw")  # apart from the real snapshots the search uses
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,
        cache=ResponseCache(settings.data_dir / "spike-cache", timedelta(hours=1)),
    ) as client:
        timings = parcel_spike.run_spike(client, config, store, parcel_id, name_query=name_query)
    table = Table("step", "pass", "s", "ok", "MB", "detail")
    for t in timings:
        table.add_row(
            t.step,
            str(t.pass_no),
            f"{t.seconds:.2f}",
            "ok" if t.ok else "FAIL",
            f"{t.peak_rss_mb:.0f}",
            t.detail,
        )
    console.print(table)
    for line in parcel_spike.summary_lines(timings):
        console.print(line)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps([t.__dict__ for t in timings], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        console.print(f"Saved: {out}")


def _load_aoi_or_none() -> Any:
    """The area of interest from the database, or None when the database is not reachable or has none."""
    from dclh_pipeline.db import session_scope
    from dclh_pipeline.parcel_check import load_aoi

    try:
        with session_scope() as session:
            return load_aoi(session)
    except Exception:  # the CLI works without a database; the parcel is then not labelled in or out
        return None


class _ConsoleCheckReporter:
    def step_start(self, parcel: int | None, key: str) -> None:
        pass

    def step_end(
        self,
        parcel: int | None,
        key: str,
        *,
        ok: bool,
        seconds: float,
        finding: str | None = None,
        error: str | None = None,
    ) -> None:
        mark = "[green]ok[/green]" if ok else "[red]BŁĄD[/red]"
        console.print(f"  {key:<11} {mark} {seconds:>6.1f} s  {finding or error or ''}")


def _print_check(result: Any) -> None:
    parcel, score = result.parcel, result.scored.score
    outside = "  [yellow]poza obszarem zainteresowania[/yellow]" if result.outside_aoi else ""
    console.print(
        f"\n[bold]{parcel.parcel_id}[/bold]  {parcel.commune}, obręb {parcel.obreb}, "
        f"{parcel.area_ha:.2f} ha{outside}"
    )
    console.print(f"[bold]{result.verdict.text_pl}[/bold]")
    console.print(
        f"wynik {score.score:.1f} (do {score.upside_score:.1f}), kompletność {score.completeness:.0%}, "
        f"moc {score.estimated_mw or 0:.0f} MW, sieć: {score.grid_badge.value}"
    )
    for exclusion in score.exclusions:
        console.print(f"  [red]{exclusion.mode}[/red] {exclusion.reason_pl}")
    for item in score.items:
        console.print(f"  {item.category:<13} {item.points:>6.1f}/{item.max_points:<5.1f} {item.label_pl}")
    for unknown in result.unknowns:
        console.print(f"  [yellow]?[/yellow] {unknown.label_pl}: {unknown.why_pl}")
    for step in result.next_steps:
        console.print(f"  -> {step.title_pl}")
