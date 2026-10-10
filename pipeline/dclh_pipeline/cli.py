"""`dclh` command-line interface."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dclh_pipeline import __version__
from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.discovery import STATUS_ICON, discover, render_markdown, save_report, update_sources_doc
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import REPO_ROOT, get_settings

app = typer.Typer(help="DC Land Hunter pipeline", no_args_is_help=True)
console = Console()


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


if __name__ == "__main__":
    app()
