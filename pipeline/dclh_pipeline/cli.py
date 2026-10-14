"""`dclh` command-line interface."""

from __future__ import annotations

import json
import logging
import os
import resource
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dclh_pipeline import __version__, capture, followup
from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.discovery import STATUS_ICON, discover, render_markdown, save_report, update_sources_doc
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import REPO_ROOT, get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.manual import MANUAL_CAPACITY, MANUAL_POINTS
from dclh_pipeline.sources.osm_power import OsmPowerConnector, summarize_grid
from dclh_pipeline.sources.prg import PrgConnector

app = typer.Typer(help="DC Land Hunter pipeline", no_args_is_help=True)
console = Console()

# Connectors that `dclh fetch` can run without the database.
CONNECTORS: dict[str, type[Source]] = {"prg": PrgConnector, "osm_power": OsmPowerConnector}


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


@app.command("discover-followup")
def discover_followup_cmd(
    out: Annotated[Path, typer.Option(help="Report directory")] = REPO_ROOT / "data" / "discovery",
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
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"followup-{datetime.now(UTC):%Y-%m-%dT%H%M}.md"
    target.write_text(report, encoding="utf-8")
    console.print(report)
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
        bool, typer.Option("--write", help="Replace the sites table with this run (needs the database)")
    ] = False,
) -> None:
    """Search one powiat: grid seed, EGiB parcels, clusters, scores. Run from Poland.

    Needs saved snapshots of `prg` and `osm_power` (`dclh fetch prg`, `dclh fetch osm_power`). Parcels are
    downloaded now. Prints the funnel counts and timings so a first run also measures the cost (R7).
    """
    from dclh_pipeline.model.features import SiteLayers, build_grid_index
    from dclh_pipeline.search import run_search
    from dclh_pipeline.sources.osm_power import to_grid

    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise typer.BadParameter("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    cfg = load_scoring()
    store = SnapshotStore(settings.raw_dir)

    prg_snapshot, osm_snapshot = store.latest("prg"), store.latest("osm_power")
    for name, snap in (("prg", prg_snapshot), ("osm_power", osm_snapshot)):
        if snap is None:
            raise typer.BadParameter(f"no saved snapshot of {name!r}; run `dclh fetch {name}` first")
    assert prg_snapshot is not None and osm_snapshot is not None

    admin = PrgConnector(config.get("prg")).parse(prg_snapshot)
    units = admin[
        (admin["level"] == "powiat") & (admin["teryt"].astype(str).str.lstrip("0") == powiat.lstrip("0"))
    ]
    if units.empty:
        raise typer.BadParameter(f"no powiat with TERYT {powiat!r} in the PRG snapshot")
    area = units.geometry.iloc[0]
    console.print(f"powiat {units.iloc[0]['teryt']} {units.iloc[0]['name']} · {area.area / 1e6:,.0f} km²")

    substations, lines = to_grid(OsmPowerConnector(config.get("osm_power")).parse(osm_snapshot))
    grid = build_grid_index(substations, lines, as_of=osm_snapshot.fetched_at, source_id="osm_power")
    console.print(f"grid: {len(substations)} substations, {len(lines)} lines (all Poland)")

    egib_spec = config.get("egib")
    egib_as_of = None

    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:

        def fetch_parcels(tiles):  # type: ignore[no-untyped-def]
            nonlocal egib_as_of
            connector = EgibConnector(egib_spec, bboxes=tiles)
            snapshot = connector.fetch(client, store)
            egib_as_of = snapshot.fetched_at
            return connector.parse(snapshot)

        result = run_search(area, SiteLayers(grid=grid), cfg, fetch_parcels, parcels_as_of=None)

    for label, value in result.report.lines():
        console.print(f"{label:<44} {value:>10}")
    console.print(
        f"peak memory {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB"
        "  (layers still missing: zoning, constraints, housing, DSO/PSE data)"
    )
    top = sorted(result.scored, key=lambda s: -s.score.score)[:10]
    for item in top:
        console.print(
            f"{item.site.site_id} {item.site.area_ha:6.1f} ha {item.score.score:5.1f} pkt"
            f"{' WYKLUCZONA' if item.score.excluded else ''}"
        )
    if write:
        from datetime import UTC, datetime

        from dclh_pipeline.db import session_scope
        from dclh_pipeline.scoring.run import write_sites

        with session_scope() as session:
            count = write_sites(session, result.scored, scored_at=datetime.now(UTC))
        console.print(f"wrote {count} sites")


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


aoi_app = typer.Typer(help="Area of interest (brief 2.1)", no_args_is_help=True)
app.add_typer(aoi_app, name="aoi")


@aoi_app.command("build")
def aoi_build_cmd() -> None:
    """Build the AOI from the saved PRG and OSM snapshots, write data/aoi/aoi.geojson and load the database.

    Needs the saved `prg` and `osm_power` snapshots (`dclh fetch`). Prints the share of each voivodeship
    inside the AOI, as a plausibility check. Needs the database.
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
    prg = PrgConnector(config.get("prg")).parse(prg_snapshot)
    vistula = aoi_build.read_vistula(osm_snapshot.path)
    aoi = aoi_build.build(aoi_build.country_geometry(prg), vistula)
    out = REPO_ROOT / "data" / "aoi" / "aoi.geojson"
    aoi_build.write_geojson(aoi, out)
    with session_scope() as session:
        aoi_build.load_aoi(
            session,
            aoi,
            note=f"PRG {prg_snapshot.fetched_at:%Y-%m-%d}, OSM {osm_snapshot.fetched_at:%Y-%m-%d}",
        )
    console.print(f"AOI {aoi.area / 1e6:,.0f} km² · written to {out}")
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


@app.command("worker")
def worker_cmd(
    once: Annotated[bool, typer.Option(help="Process pending jobs, then exit")] = False,
    interval: Annotated[float, typer.Option(help="Seconds between polls")] = 10.0,
) -> None:
    """Poll the jobs table and execute jobs one at a time."""
    from dclh_pipeline.jobs import work

    executed = work(once=once, interval_s=interval)
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
