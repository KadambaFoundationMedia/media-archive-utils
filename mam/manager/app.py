"""FastAPI application for the media manager."""

from __future__ import annotations

import logging
import re
import shutil
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.local_db import migrate, session_scope
from mam.helpers.local_db import models as m
from mam.helpers.local_db.repo import media_kind
from mam.manager import queries
from mam.manager.queue import JobQueue, enqueue
from mam.manager.tools import TOOLS, menu_tools, pipeline_start

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

templates = Jinja2Templates(directory=HERE / "templates")

STATUS_COLORS = {
    "Not processed": "secondary",
    "Corrupted": "danger",
    "Excluded": "dark",
    "Skipped": "light",
    "Processing complete": "success",
}


def status_color(status: str) -> str:
    if status in STATUS_COLORS:
        return STATUS_COLORS[status]
    if status.endswith(" Review"):
        return "warning"
    if status.endswith(" Failed"):
        return "danger"
    if status.startswith(("Processing ", "Queued ")):
        return "info"
    if status.endswith(" Done"):
        return "success"
    return "secondary"


SOURCE_LABELS = {
    "filename": "Filename",
    "folder": "Folder name",
    "tag": "Embedded tags (ID3)",
    "siblings": "Other files in the folder",
    "media": "Baserow media table",
    "travel": "Travel schedule",
    "place": "Known place (Baserow)",
    "rule": "Naming rule",
}
FIELD_LABELS = {"when": "WHEN", "what": "WHAT", "where": "WHERE", "who": "WHO"}

# The views at the top of the Status filter, in the same order (file list status guide).
VIEW_HELP = {
    "Needs attention": "Files waiting for a review, failed files and corrupted files. The list "
    "opens on this view.",
    "All files": "Every file, whatever its status; choose it, for example, to start a tool.",
    "Needs review": "Files with a <Tool> Review status.",
    "Failed": "Files with a <Tool> Failed status.",
}

# Shown on the status badges and in the status guide on the file list, in the order of
# the "One status" list (queries.all_statuses).
STATUS_HELP = {
    "Not processed": "Imported, but no tool has changed it yet. A dry run does not change the "
    "status; its proposed name is shown with a 'dry run' label.",
    "Queued": "Waiting in the job queue for a tool.",
    "Processing": "A tool is working on it now.",
    "Done": "The tool finished; the file waits for the next tool in the pipeline.",
    "Review": "The tool needs your decision. Open it from the Review page.",
    "Failed": "The tool hit an error (see Jobs). Fix the cause and retry.",
    "Skipped": "The step does not apply to this file.",
    "Excluded": "A reviewer stopped all processing of this file.",
    "Corrupted": "The file cannot be read or decoded.",
    "Processing complete": "Every tool in the pipeline has finished.",
}


def status_help(status: str) -> str:
    if status in STATUS_HELP:
        return STATUS_HELP[status]
    for key in ("Queued", "Processing", "Done", "Review", "Failed"):
        if key in status.split():
            return STATUS_HELP[key]
    return ""


templates.env.globals.update(
    status_color=status_color,
    status_help=status_help,
    status_guide=STATUS_HELP,
    view_guide=VIEW_HELP,
    tools=TOOLS,
    source_labels=SOURCE_LABELS,
    field_labels=FIELD_LABELS,
)
templates.env.filters["basename"] = lambda p: Path(p).name if p else ""
templates.env.filters["dirname"] = lambda p: str(Path(p).parent) if p else ""


def rel_folder(path: str | None) -> str:
    """Folder of a file relative to its import root: 'Sample files / 2012 / Radhadesh Jan 2012'."""
    if not path:
        return ""
    folder = Path(path).parent
    for label, root in get_settings().import_roots:
        root = root.resolve()
        if folder == root or root in folder.parents:
            parts = folder.relative_to(root).parts
            return " / ".join([label, *parts])
    return str(folder)


templates.env.filters["rel_folder"] = rel_folder


def localtime(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Show a stored UTC time in the computer's own time zone."""
    if value is None:
        return ""
    if value.tzinfo is None:  # SQLite returns naive datetimes; they are UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime(fmt)


templates.env.filters["localtime"] = localtime


def filesize(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


templates.env.filters["filesize"] = filesize


def asset_version() -> str:
    """Changes whenever a static file changes, so browsers never use an outdated copy."""
    static = HERE / "static"
    return str(int(max((p.stat().st_mtime for p in static.iterdir()), default=0)))


templates.env.globals["asset_version"] = asset_version


def category_style(name: str | None) -> str:
    """Inline style for a category pill, in the color from Baserow category_terms_folder."""
    if not name:
        return ""
    from mam.manager.colors import badge_style
    from mam.tools.renamer.knowledge import reference_if_ready

    ref = reference_if_ready()  # never wait for Baserow just for a color
    category = ref.matcher.by_name(name) if ref else None
    return badge_style(category.color) if category else ""


templates.env.globals["category_style"] = category_style


def create_app(start_queue: bool = True) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        migrate()
        from mam.tools.renamer.knowledge import preload_reference

        preload_reference()  # so the first page with files does not wait for Baserow
        queue = JobQueue(settings)
        app.state.queue = queue
        if start_queue:
            queue.start()
        yield
        if start_queue:
            queue.stop()

    app = FastAPI(title="Media Archive Manager", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, name: str, **context) -> HTMLResponse:
        from mam.helpers.local_db.app_settings import baserow_writes_enabled

        context["baserow_on"] = baserow_writes_enabled()
        with session_scope() as session:
            context.setdefault("counts", queries.counts(session))
        if "rows" in context and "active" not in context:
            # Keep refreshing the list while any job (also a dry run) is queued or running.
            context["active"] = context["counts"]["jobs"] > 0
        context["settings"] = settings
        return templates.TemplateResponse(request, name, context)

    def notify(request: Request) -> None:
        queue: JobQueue | None = getattr(request.app.state, "queue", None)
        if queue:
            queue.notify()

    # ------------------------------------------------------------- file list
    @app.get("/", response_class=HTMLResponse)
    def files_page(request: Request):
        flt = queries.FileFilter.from_params(request.query_params)
        with session_scope() as session:
            rows, total = queries.list_files(session, flt)
            facets = queries.facet_values(session)
        return render(
            request,
            "files.html",
            rows=rows,
            total=total,
            flt=flt,
            facets=facets,
            page_size=queries.PAGE_SIZE,
            menu=menu_tools(),
        )

    @app.get("/files/rows", response_class=HTMLResponse)
    def files_rows(request: Request):
        flt = queries.FileFilter.from_params(request.query_params)
        with session_scope() as session:
            rows, total = queries.list_files(session, flt)
        return render(
            request,
            "_file_rows.html",
            rows=rows,
            total=total,
            flt=flt,
            page_size=queries.PAGE_SIZE,
        )

    @app.post("/actions")
    async def run_action(request: Request):
        form = await request.form()
        tool = form.get("tool", "")
        # Two buttons: "Preview (dry run)" sends mode=preview, "Run" sends mode=run.
        dry_run = form.get("dry_run") == "on" or form.get("mode") == "preview"
        pipeline = tool == "__pipeline"
        if pipeline:
            tool = pipeline_start()
        if tool not in TOOLS or not TOOLS[tool].per_file:
            raise HTTPException(400, f"Unknown tool {tool}")
        with session_scope() as session:
            if form.get("scope") == "all":
                # An action without a status filter applies to all files.
                flt = queries.FileFilter.from_params(form, default_status="__all")
                ids = queries.matching_ids(session, flt)
            else:
                ids = [int(i) for i in form.getlist("ids")]
            queued = enqueue(session, tool, ids, dry_run=dry_run, pipeline=pipeline)
        notify(request)
        label = "Full pipeline" if pipeline else TOOLS[tool].label
        msg = f"{label}: {queued} file(s) queued" + (" as a dry run" if dry_run else "")
        back = re.sub(r"[?&](msg|live)=[^&]*", "", form.get("back") or "/")
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}msg={msg}&live=1", status_code=303)

    # ----------------------------------------------------------- file detail
    @app.get("/files/{file_id}", response_class=HTMLResponse)
    def file_page(request: Request, file_id: int):
        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            context = {
                "f": record,
                "aliases": list(record.aliases),
                "children": session.scalars(
                    select(m.MediaFile).where(m.MediaFile.parent_id == file_id)
                ).all(),
                "history": session.scalars(
                    select(m.StatusHistory)
                    .where(m.StatusHistory.media_file_id == file_id)
                    .order_by(m.StatusHistory.id.desc())
                ).all(),
                "events": session.scalars(
                    select(m.FileEvent)
                    .where(m.FileEvent.media_file_id == file_id)
                    .order_by(m.FileEvent.id.desc())
                    .limit(50)
                ).all(),
                "jobs": session.scalars(
                    select(m.Job).where(m.Job.media_file_id == file_id).order_by(m.Job.id.desc())
                ).all(),
                "evidence": session.scalars(
                    select(m.WEvidence)
                    .where(m.WEvidence.media_file_id == file_id)
                    .order_by(m.WEvidence.field, m.WEvidence.score.desc())
                ).all(),
                "reviews": session.scalars(
                    select(m.Review)
                    .where(m.Review.media_file_id == file_id)
                    .order_by(m.Review.id.desc())
                ).all(),
                "baserow_writes": session.scalars(
                    select(m.BaserowWriteLog)
                    .where(m.BaserowWriteLog.media_file_id == file_id)
                    .order_by(m.BaserowWriteLog.id.desc())
                ).all(),
            }
        from mam.manager.edit_routes import form_context
        from mam.tools.renamer.editing import initial_values

        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            edit_context = form_context(record, initial_values(record))
        return render(
            request,
            "file.html",
            menu=menu_tools(),
            mode="file",
            review_id=None,
            **context,
            **edit_context,
        )

    @app.get("/files/{file_id}/activity", response_class=HTMLResponse)
    def file_activity(request: Request, file_id: int):
        """Polled by the file page while a job runs; reloads the page when it is done."""
        with session_scope() as session:
            running = session.scalars(
                select(m.Job)
                .where(m.Job.media_file_id == file_id, m.Job.state.in_(("queued", "running")))
                .order_by(m.Job.id)
            ).all()
        if not running:
            return HTMLResponse("", headers={"HX-Redirect": f"/files/{file_id}"})
        return render(request, "_file_activity.html", f_id=file_id, running=running)

    @app.get("/files/{file_id}/audio")
    def file_audio(file_id: int, which: str = "original"):
        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            path = record.processed_path if which == "processed" else record.original_path
        if not path or not Path(path).exists():
            raise HTTPException(404, "File not available")
        return FileResponse(path)

    # ------------------------------------------------------------------ jobs
    @app.get("/nav/badges", response_class=HTMLResponse)
    def nav_badges(request: Request):
        """Navbar counts, swapped out of band while jobs run (see _nav_badges.html)."""
        return render(request, "_nav_badges.html", oob=True)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return render(request, "jobs.html", **_jobs_context(request))

    @app.get("/jobs/rows", response_class=HTMLResponse)
    def jobs_rows(request: Request):
        return render(request, "_job_rows.html", **_jobs_context(request))

    def _jobs_context(request: Request) -> dict:
        state = request.query_params.get("state", "")
        with session_scope() as session:
            stmt = select(m.Job).order_by(m.Job.id.desc()).limit(200)
            if state:
                stmt = stmt.where(m.Job.state == state)
            jobs = session.scalars(stmt).all()
            names = {
                f.id: f.current_name or Path(f.original_path or "").name
                for f in session.scalars(
                    select(m.MediaFile).where(
                        m.MediaFile.id.in_({j.media_file_id for j in jobs if j.media_file_id})
                    )
                )
            }
        active = any(j.state in ("queued", "running") for j in jobs)
        return {"jobs": jobs, "names": names, "state": state, "active": active}

    @app.post("/jobs/{job_id}/retry")
    def retry_job(request: Request, job_id: int):
        with session_scope() as session:
            job = session.get(m.Job, job_id)
            if job is None:
                raise HTTPException(404)
            if job.tool == "scan":
                enqueue(session, "scan", dry_run=job.dry_run, params=job.params)
            else:
                enqueue(
                    session,
                    job.tool,
                    [job.media_file_id],
                    dry_run=job.dry_run,
                    pipeline=job.pipeline,
                    params=job.params,
                )
        notify(request)
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/cancel")
    def cancel_queued(request: Request):
        with session_scope() as session:
            for job in session.scalars(select(m.Job).where(m.Job.state == "queued")):
                job.state = "cancelled"
                if job.media_file_id and not job.dry_run and TOOLS[job.tool].sets_status:
                    record = session.get(m.MediaFile, job.media_file_id)
                    if record.status.startswith("Queued "):
                        from mam.helpers.local_db.repo import set_status

                        last = session.scalar(
                            select(m.StatusHistory.old_status)
                            .where(m.StatusHistory.media_file_id == record.id)
                            .order_by(m.StatusHistory.id.desc())
                        )
                        set_status(session, record, last or "Not processed", job.tool, "cancelled")
        return RedirectResponse("/jobs", status_code=303)

    # ---------------------------------------------------------------- import
    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request, root: int = 0, path: str = ""):
        roots = settings.import_roots
        if not 0 <= root < len(roots):
            raise HTTPException(404)
        label, base = roots[root]
        current = _safe_join(base, path)
        folders, media_count = [], 0
        if current.is_dir():
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    folders.append(child.name)
                elif media_kind(child):
                    media_count += 1
        crumbs = []
        parts = Path(path).parts if path else ()
        for i in range(len(parts)):
            crumbs.append((parts[i], "/".join(parts[: i + 1])))
        return render(
            request,
            "import.html",
            roots=roots,
            root=root,
            root_label=label,
            path=path,
            current=current,
            exists=current.is_dir(),
            folders=folders,
            media_count=media_count,
            crumbs=crumbs,
        )

    @app.post("/import")
    def import_folder(
        request: Request, root: Annotated[int, Form()], path: Annotated[str, Form()] = ""
    ):
        _, base = settings.import_roots[root]
        folder = _safe_join(base, path)
        with session_scope() as session:
            enqueue(session, "scan", dry_run=False, params={"path": str(folder)})
        notify(request)
        return RedirectResponse(
            f"/jobs?msg=Scanning {folder.name or folder}&live=1", status_code=303
        )

    @app.post("/upload")
    async def upload(request: Request, files: list[UploadFile]):
        inbox = settings.inbox_path
        inbox.mkdir(parents=True, exist_ok=True)
        saved = 0
        for upload_file in files:
            name = Path(upload_file.filename or "").name
            if not name or media_kind(Path(name)) is None:
                continue
            target = inbox / name
            n = 2
            while target.exists():
                target = inbox / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            with target.open("wb") as out:
                shutil.copyfileobj(upload_file.file, out)
            saved += 1
        with session_scope() as session:
            enqueue(session, "scan", dry_run=False, params={"path": str(inbox)})
        notify(request)
        return RedirectResponse(
            f"/jobs?msg={saved} file(s) uploaded to the inbox&live=1", status_code=303
        )

    # -------------------------------------------------------------- settings
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        from mam.manager.maintenance import summary

        return render(request, "settings.html", info=summary(settings))

    @app.post("/settings/baserow-writes")
    async def toggle_baserow_writes(request: Request):
        from mam.helpers.local_db.app_settings import set_baserow_writes

        form = await request.form()
        enabled = form.get("enabled") == "on"
        set_baserow_writes(enabled)
        back = re.sub(r"[?&](msg|live)=[^&]*", "", form.get("back") or "/")
        sep = "&" if "?" in back else "?"
        msg = (
            "Baserow writes are ON: runs now write to the live media table"
            if enabled
            else "Baserow writes are off: runs only log their Baserow rows"
        )
        return RedirectResponse(f"{back}{sep}msg={msg}", status_code=303)

    @app.post("/settings/refresh-baserow")
    def refresh_baserow():
        """Reload categories, places, travel schedule and media index from Baserow now."""
        from mam.tools.renamer.knowledge import get_reference

        try:
            ref = get_reference(refresh=True)
        except Exception as exc:
            return RedirectResponse(
                f"/settings?error=Baserow refresh failed: {exc}", status_code=303
            )
        msg = (
            f"Baserow data refreshed: {len(ref.place_options)} places, "
            f"{len(ref.category_options)} categories, {len(ref.stays)} travel stays"
        )
        return RedirectResponse(f"/settings?msg={msg}", status_code=303)

    @app.post("/settings/reset")
    async def settings_reset(request: Request):
        from mam.errors import MamError
        from mam.manager.maintenance import ResetOptions, reset

        form = await request.form()
        if (form.get("confirm") or "").strip() != "RESET":
            return RedirectResponse("/settings?error=Type RESET to confirm", status_code=303)
        options = ResetOptions(
            processed_files=form.get("processed_files") == "on",
            learned=form.get("learned") == "on",
            caches=form.get("caches") == "on",
        )
        try:
            result = reset(options, settings)
        except MamError as exc:
            return RedirectResponse(f"/settings?error={exc}", status_code=303)
        files = sum(v for k, v in result.rows_deleted.items() if k == "media_file")
        msg = (
            f"Reset done: {files} file record(s) and {result.files_deleted} processed file(s) "
            f"removed. Backup: {result.backup}"
        )
        return RedirectResponse(f"/settings?msg={msg}", status_code=303)

    @app.get("/health")
    def health() -> Response:
        return Response("ok")

    from mam.manager.edit_routes import register_edit_routes
    from mam.manager.review import register_review_routes

    register_review_routes(app, render, notify)
    register_edit_routes(app, render, notify)
    return app


def _safe_join(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    base = base.resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "Path outside the import root")
    return target
