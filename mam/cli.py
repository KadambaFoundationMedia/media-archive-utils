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

console = Console()

DryRun = Annotated[
    bool, typer.Option("--dry-run/--no-dry-run", help="Report what would happen without writing.")
]


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
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


@app.command()
def rename(
    path: Path,
    dry_run: DryRun = True,
) -> None:
    """Run the Renamer on a file or folder from the command line (dry run by default)."""
    from mam.helpers.local_db import session_scope
    from mam.helpers.local_db.repo import register_original
    from mam.manager.queue import JobQueue, enqueue
    from mam.manager.scanner import scan_folder

    _ready_db()
    with session_scope() as session:
        if path.is_dir():
            scan_folder(session, path)
            from sqlalchemy import select

            from mam.helpers.local_db import models as m

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
        jobs = enqueue(session, "renamer", ids, dry_run=dry_run)
    queue = JobQueue()
    queue.start()
    import time

    from sqlalchemy import func, select

    from mam.helpers.local_db import models as m

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
            select(m.Job).where(m.Job.tool == "renamer").order_by(m.Job.id.desc()).limit(jobs)
        ).all()
        for job in reversed(rows):
            line = job.result["summary"] if job.result else job.error.splitlines()[0]
            console.print(f"[{'red' if job.state == 'failed' else 'green'}]{job.state}[/] {line}")
            for flag in (job.result or {}).get("flags", []):
                console.print(f"   [yellow]⚑ {flag}[/yellow]")
