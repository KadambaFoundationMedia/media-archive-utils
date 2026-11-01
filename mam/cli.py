"""`mam` command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mam.config import get_settings
from mam.logging import setup_logging

app = typer.Typer(help="Media Archive Manager tools and helpers.", no_args_is_help=True)
db_app = typer.Typer(help="Local database.", no_args_is_help=True)
audio_app = typer.Typer(help="Audio optimizer helper.", no_args_is_help=True)
baserow_app = typer.Typer(help="Baserow helper.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(audio_app, name="audio")
app.add_typer(baserow_app, name="baserow")
place_country_app = typer.Typer(help="Baserow place_country table.", no_args_is_help=True)
app.add_typer(place_country_app, name="place-country")

console = Console()

DryRun = Annotated[
    bool, typer.Option("--dry-run/--no-dry-run", help="Report what would happen without writing.")
]


def _version_callback(value: bool) -> None:
    if value:
        from mam import __version__

        typer.echo(f"mam {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    setup_logging(verbose)


def _ready_db() -> None:
    from mam.helpers.local_db import migrate

    migrate()


@app.command()
def doctor() -> None:
    """Check the environment: binaries, models, .env, local DB and Baserow (read-only)."""
    from mam.doctor import run_checks

    checks = run_checks(get_settings())
    table = Table("Check", "Status", "Detail")
    failed = False
    for check in checks:
        if check.ok:
            status = "[green]ok[/green]"
        elif check.required:
            status, failed = "[red]FAIL[/red]", True
        else:
            status = "[yellow]warn[/yellow]"
        table.add_row(check.name, status, check.detail)
    console.print(table)
    raise typer.Exit(1 if failed else 0)


# ------------------------------------------------------------------------ db
@db_app.command("migrate")
def db_migrate() -> None:
    """Create or upgrade the local database schema."""
    _ready_db()
    console.print(f"Database ready: {get_settings().local_db_path}")


@db_app.command("export")
def db_export(out_dir: Annotated[Path, typer.Argument()] = Path(".")) -> None:
    """Export the local DB (SQLite copy plus one JSON file per table)."""
    from mam.helpers.local_db import get_engine
    from mam.helpers.local_db.repo import export_db

    _ready_db()
    console.print(f"Exported to {export_db(get_engine(), out_dir)}")


@db_app.command("import")
def db_import(source: Path) -> None:
    """Replace the local DB with an export; the current DB is backed up first."""
    from mam.helpers.local_db.repo import import_db

    backup = import_db(source, get_settings().local_db_path)
    _ready_db()
    console.print(f"Imported {source}" + (f" (backup: {backup})" if backup else ""))


# ----------------------------------------------------------------- organizer
@app.command()
def organize(
    src: Path,
    what: Annotated[
        str | None, typer.Option(help="The WHAT used to pick the category folder.")
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="Final filename (default: current name).")
    ] = None,
    dry_run: DryRun = True,
) -> None:
    """Copy a file into processed/<category-folder>/ (debug command for the file organizer)."""
    from mam.helpers.baserow import BaserowClient
    from mam.helpers.file_organizer import CategoryMatcher, FileOrganizer

    _ready_db()
    with BaserowClient() as client:
        matcher = CategoryMatcher(client.category_terms())
    result = FileOrganizer(matcher).organize(
        src, name or src.name, what, tool="organizer-cli", dry_run=dry_run
    )
    console.print(
        f"{result.action}{' (dry-run)' if dry_run else ''}: {src}\n  -> {result.dst}\n"
        f"  category: {result.category or '-'}  duplicate: {result.duplicate}  "
        f"clash/review: {result.clash}"
    )


# --------------------------------------------------------------------- audio
@audio_app.command("probe")
def audio_probe(path: Path) -> None:
    """Show codec, bitrate, sample rate, channels and duration."""
    from dataclasses import asdict

    from mam.helpers.audio_optimizer import probe

    console.print_json(json.dumps(asdict(probe(path)), default=str))


@audio_app.command("measure")
def audio_measure(path: Path) -> None:
    """Measure integrated loudness, true peak and loudness range."""
    from mam.helpers.audio_optimizer import measure

    loud = measure(path)
    console.print(
        f"I {loud.integrated:.1f} LUFS  TP {loud.true_peak:.1f} dBTP  LRA {loud.lra:.1f} LU"
    )


@audio_app.command("normalize")
def audio_normalize(src: Path, dst: Path, dry_run: DryRun = True) -> None:
    """Normalize the whole recording to -18 LUFS / -1 dBTP at the source format."""
    from mam.helpers.audio_optimizer import normalize_whole

    result = normalize_whole(src, dst, dry_run=dry_run)
    console.print_json(json.dumps(result.to_dict(), default=str))


@audio_app.command("verify")
def audio_verify(path: Path) -> None:
    """Strict full decode; exits 1 if the file has decode errors."""
    from mam.helpers.audio_optimizer import verify_decode

    errors = verify_decode(path)
    for line in errors[:20]:
        console.print(f"[red]{line}[/red]")
    console.print("clean" if not errors else f"{len(errors)} decode errors")
    raise typer.Exit(1 if errors else 0)


# ------------------------------------------------------------------- baserow
@baserow_app.command("schema")
def baserow_schema(
    table: Annotated[str, typer.Argument(help="media | travel | category")] = "media",
) -> None:
    """Show the live column schema and select options of a table."""
    from mam.helpers.baserow import BaserowClient

    with BaserowClient() as client:
        table_id = {
            "media": client.media_table,
            "travel": client.travel_table,
            "category": client.category_table,
        }[table]
        out = Table("Column", "Type", "Options")
        for f in client.fields(table_id).values():
            out.add_row(f.name, f.type, ", ".join(f.options)[:120])
        console.print(out)


@baserow_app.command("undo")
def baserow_undo(log_id: int, dry_run: DryRun = True) -> None:
    """Revert a logged Baserow write."""
    from mam.helpers.baserow import BaserowClient

    _ready_db()
    with BaserowClient(apply=not dry_run) as client:
        result = client.undo(log_id, dry_run=dry_run)
    console.print(
        f"undo{' (dry-run)' if result.dry_run else ''}: {result.payload or 'row deleted'}"
    )


@place_country_app.command("sync")
def place_country_sync(
    apply: Annotated[bool, typer.Option("--apply", help="Write to Baserow (live).")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """One-time fill of place_country (one row per media place) and tidy media countries.

    Without --apply nothing is written: the rows are only listed.
    """
    import yaml

    from mam.helpers.baserow import BaserowClient
    from mam.tools.renamer.knowledge import ALIASES_FILE, fetch_raw
    from mam.tools.renamer.place_country import apply_sync, plan_sync

    _ready_db()
    raw = fetch_raw()
    aliases = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8"))
    plan = plan_sync(raw, aliases.get("no_country") or [])
    out = Table("Place", "Country", "ccTLD", "From", "Action")
    for row in plan.rows:
        out.add_row(row.place, row.country or "–", row.cc or "–", row.source, row.action)
    console.print(out)
    fixes = Table("Media row", "Place", "Country now", "Becomes", "Why")
    for fix in plan.media:
        fixes.add_row(str(fix.row_id), fix.place or "–", fix.old or "–", fix.new, fix.reason)
    console.print(fixes)
    if plan.skipped:
        console.print("No row (other spelling of a place): " + ", ".join(plan.skipped))
    if plan.unknown:
        console.print("[yellow]No country found:[/yellow] " + ", ".join(plan.unknown))
    if not apply:
        console.print("Dry run: nothing written. Run again with --apply to write.")
        return
    if not yes:
        typer.confirm("Write these rows to the live Baserow?", abort=True)
    with BaserowClient(apply=True) as client:
        done = apply_sync(plan, client, dry_run=False)
    console.print(f"Written: {done}")


# ------------------------------------------------------------------- manager
@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1")] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload on code changes (development)")] = False,
) -> None:
    """Start the media manager at http://127.0.0.1:<port>."""
    import uvicorn

    console.print(f"Media manager on http://127.0.0.1:{port}")
    uvicorn.run(
        "mam.manager.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        reload=reload,
        log_level="info",
    )


def _run_tool_cli(tool: str, path: Path, dry_run: bool) -> None:
    """Queue `tool` for a file or folder, run the queue until it is empty, print the results."""
    import time

    from sqlalchemy import func, select

    from mam.helpers.local_db import models as m
    from mam.helpers.local_db import session_scope
    from mam.helpers.local_db.repo import register_original
    from mam.manager.queue import JobQueue, enqueue
    from mam.manager.scanner import scan_folder

    _ready_db()
    with session_scope() as session:
        if path.is_dir():
            scan_folder(session, path)
            ids = list(
                session.scalars(
                    select(m.MediaFile.id).where(
                        m.MediaFile.original_path.like(f"{path.resolve()}/%")
                    )
                )
            )
        else:
            record = register_original(session, path)
            ids = [record.id] if record else []
        jobs = enqueue(session, tool, ids, dry_run=dry_run)
    queue = JobQueue()
    queue.start()
    while True:
        with session_scope() as session:
            left = session.scalar(
                select(func.count(m.Job.id)).where(m.Job.state.in_(("queued", "running")))
            )
        if not left:
            break
        time.sleep(0.5)
    queue.stop()
    with session_scope() as session:
        rows = session.scalars(
            select(m.Job).where(m.Job.tool == tool).order_by(m.Job.id.desc()).limit(jobs)
        ).all()
        for job in reversed(rows):
            line = job.result["summary"] if job.result else job.error.splitlines()[0]
            console.print(f"[{'red' if job.state == 'failed' else 'green'}]{job.state}[/] {line}")
            for flag in (job.result or {}).get("flags", []):
                console.print(f"   [yellow]⚑ {flag}[/yellow]")
            for reason in (job.result or {}).get("reasons", []):
                console.print(f"   {reason}")
            line = ((job.result or {}).get("features") or {}).get("timeline")
            if line:
                console.print(
                    f"   every 10 s (m music, s speech, . silence): {line}", soft_wrap=True
                )


@app.command()
def rename(
    path: Path,
    dry_run: DryRun = True,
) -> None:
    """Run the Renamer on a file or folder from the command line (dry run by default)."""
    _run_tool_cli("renamer", path, dry_run)


@app.command("kirtan-split")
def kirtan_split(
    path: Path,
    dry_run: DryRun = True,
) -> None:
    """Find where the kirtan ends in a recording and split it (dry run by default).

    A dry run reads any file. A real run needs the renamed copy in processed-files, so run
    `mam rename --no-dry-run` first (the media manager does both in its pipeline).
    """
    _run_tool_cli("kirtan-splitter", path, dry_run)


@app.command()
def reset(
    processed_files: Annotated[bool, typer.Option(help="Delete the processed copies.")] = True,
    learned: Annotated[bool, typer.Option(help="Also forget learned corrections.")] = False,
    caches: Annotated[bool, typer.Option(help="Also clear caches.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """Reset the media manager: clear the local database and processed copies (backup first).

    Originals and Baserow are never changed.
    """
    from mam.manager.maintenance import ResetOptions, summary
    from mam.manager.maintenance import reset as do_reset

    _ready_db()
    info = summary()
    console.print(
        f"This clears {info['files']} file records, {info['jobs']} jobs, {info['reviews']} reviews"
        + (f" and deletes {info['processed_files']} processed file(s)" if processed_files else "")
        + ". Originals and Baserow are not changed; a database backup is made first."
    )
    if not yes:
        typer.confirm("Reset the media manager?", abort=True)
    result = do_reset(ResetOptions(processed_files, learned, caches))
    console.print(f"Reset done. Backup: {result.backup}")
