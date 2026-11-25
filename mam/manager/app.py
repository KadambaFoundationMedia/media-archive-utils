"""FastAPI application for the media manager."""

from __future__ import annotations

import logging
import re
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from mam import __version__
from mam.config import get_settings
from mam.errors import MamError
from mam.helpers.local_db import migrate, session_scope
from mam.helpers.local_db import models as m
from mam.helpers.local_db.app_settings import (
    OPTION_BY_KEY,
    OPTIONS,
    default_size_setting,
    default_status_setting,
    option_groups,
    option_value,
    renamer_move_files,
    renamer_postfix,
    set_default_size,
    set_default_status,
    set_option,
    set_renamer_options,
    set_transcribe_speech_only,
    transcribe_speech_only,
)
from mam.helpers.local_db.repo import media_kind
from mam.manager import queries, stale
from mam.manager.queue import JobQueue, enqueue
from mam.manager.tools import TOOLS, menu_tools, pipeline_start
from mam.statuses import (
    SPLITTER_CLASS,
    SPLITTER_KIRTAN,
    SPLITTER_NOT_SPLIT,
    SPLITTER_SPLIT,
    SPLITTER_STATUSES,
    is_busy,
)

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

templates = Jinja2Templates(directory=HERE / "templates")

STATUS_COLORS = {
    "Not processed": "secondary",
    "Corrupted": "danger",
    "Excluded": "dark",
    "Deleted": "dark",
    "Skipped": "light",
    "Processing complete": "success",
    **{status: "success" for status in SPLITTER_STATUSES},
}


def status_color(status: str) -> str:
    if status in STATUS_COLORS:
        return STATUS_COLORS[status]
    if status.endswith(" Review"):
        return "warning"
    if status.endswith(" Failed"):
        return "danger"
    if is_busy(status):
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
    "All files": "Every file, whatever its status; choose it, for example, to start a tool.",
    "Review": "Files with a <Tool> Review status: a tool needs your decision.",
    "Failed": "Files with a <Tool> Failed status.",
}

# Shown on the status badges and in the status guide on the file list, in the order of
# the "One status" list (queries.all_statuses).
STATUS_HELP = {
    "Not processed": "Imported, but no tool has changed it yet. A dry run does not change the "
    "status; its proposed name is shown with a 'dry run' label.",
    "Queued": "Waiting in the job queue for a tool (the Status column shows which one).",
    "Processing": "A tool is working on it now.",
    "Done": "The tool finished; the file waits for the next tool in the pipeline.",
    "Review": "The tool needs your decision. Open it from the Review page.",
    "Failed": "The tool hit an error (see Jobs). Fix the cause and retry.",
    SPLITTER_CLASS: "Kirtan splitter: a class with no kirtan before it, so nothing was split.",
    SPLITTER_KIRTAN: "Kirtan splitter: singing throughout, a kirtan recording that is not split. "
    "It is filed in the Kirtan folder.",
    SPLITTER_SPLIT: "Kirtan splitter: cut into a kirtan and a class.",
    SPLITTER_NOT_SPLIT: "Kirtan splitter: a reviewer decided that it is not split.",
    "Skipped": "The step does not apply to this file.",
    "Excluded": "A reviewer stopped all processing of this file.",
    "Deleted": "The copy in processed-files was moved to the Trash. The original was not touched.",
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
    app_version=__version__,
    source_labels=SOURCE_LABELS,
    field_labels=FIELD_LABELS,
)


def _hms(seconds):
    from mam.tools.kirtan_splitter.detect import format_time

    return format_time(seconds) if isinstance(seconds, int | float) else seconds


templates.env.filters["hms"] = _hms
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


def _region_colors(record: m.MediaFile, proposal: dict) -> dict[str, str]:
    """Colors of the kirtan and class regions in the waveform editor.

    From the Baserow categories (`category_terms_folder.color`): the class takes its recording's
    category, the kirtan the category of its mantra. Fixed colors when Baserow is not loaded.
    """
    from mam.manager.colors import css_color
    from mam.tools.renamer.knowledge import reference_if_ready

    colors = {"kirtan": "#e57b24", "class": "#3d7fdb"}
    ref = reference_if_ready()  # never wait for Baserow just for a color
    if ref is None:
        return colors
    try:
        category = ref.matcher.by_name(record.category) if record.category else None
        if category and css_color(category.color):
            colors["class"] = css_color(category.color)
        from mam.tools.kirtan_splitter.phrases import mantra_choices

        mantra = proposal.get("mantra") or mantra_choices(ref.matcher)[0]
        found = ref.matcher.match(mantra)
        if found and found.category and css_color(found.category.color):
            colors["kirtan"] = css_color(found.category.color)
    except Exception:  # a color is never worth failing the page
        logging.getLogger(__name__).debug("region colors fell back", exc_info=True)
    return colors


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
        context["now_year"] = str(datetime.now().year)
        context["code_changed"] = stale.code_changed()
        with session_scope() as session:
            context.setdefault("counts", queries.counts(session))
            if request.query_params.get("live"):
                context.setdefault("activity", queries.activity(session))
            if "rows" in context:
                context["running"] = queries.active_jobs(session)
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
        flt = queries.FileFilter.from_params(
            request.query_params, default_status_setting(), default_size_setting()
        )
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
            page_sizes=queries.PAGE_SIZES,
            menu=menu_tools(),
        )

    @app.get("/files/rows", response_class=HTMLResponse)
    def files_rows(request: Request):
        flt = queries.FileFilter.from_params(
            request.query_params, default_status_setting(), default_size_setting()
        )
        with session_scope() as session:
            rows, total = queries.list_files(session, flt)
        return render(
            request,
            "_file_rows.html",
            rows=rows,
            total=total,
            flt=flt,
        )

    @app.get("/files/more", response_class=HTMLResponse)
    def files_more(request: Request, last_folder: str = "", stripe: int = 0):
        """The next batch of rows, appended to the table by the Load more button."""
        flt = queries.FileFilter.from_params(
            request.query_params, default_status_setting(), default_size_setting()
        )
        with session_scope() as session:
            rows, total = queries.list_files(session, flt)
        return render(
            request,
            "_file_more.html",
            rows=rows,
            total=total,
            flt=flt,
            last_folder=last_folder,
            stripe=stripe,
        )

    @app.post("/actions")
    async def run_action(request: Request):
        form = await request.form()
        tool = form.get("tool", "")
        # Two buttons: "Preview (dry run)" sends mode=preview, "Run" sends mode=run.
        dry_run = form.get("dry_run") == "on" or form.get("mode") == "preview"
        if tool == "__delete":
            return _delete_response(request, form, dry_run)
        pipeline = tool == "__pipeline"
        if pipeline:
            tool = pipeline_start()
        if tool not in TOOLS or not TOOLS[tool].per_file or TOOLS[tool].hidden:
            raise HTTPException(400, f"Unknown tool {tool}")
        with session_scope() as session:
            if form.get("scope") == "all":
                # An action without a status filter applies to all files.
                flt = queries.FileFilter.from_params(form, default_status="__all")
                ids = queries.matching_ids(session, flt)
            else:
                ids = [int(i) for i in form.getlist("ids")]
            waiting: list[str] = []
            queued = enqueue(
                session, tool, ids, dry_run=dry_run, pipeline=pipeline, waiting=waiting
            )
        notify(request)
        label = "Full pipeline" if pipeline else TOOLS[tool].label
        msg = f"{label}: {queued} file(s) queued" + (" as a dry run" if dry_run else "")
        if waiting:
            msg += f"; {len(waiting)} not queued: the {waiting[0]} review is still open"
        back = re.sub(r"[?&](msg|live)=[^&]*", "", form.get("back") or "/")
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}msg={msg}&live=1", status_code=303)

    def _delete_response(request: Request, form, dry_run: bool) -> RedirectResponse:
        """Delete files from the list: their processed copies go to the Trash."""
        from mam.helpers.file_organizer.trash import delete_file

        deleted, skipped, to_trash = 0, [], 0
        with session_scope() as session:
            if form.get("scope") == "all":
                flt = queries.FileFilter.from_params(form, default_status="__all")
                ids = queries.matching_ids(session, flt)
            else:
                ids = [int(i) for i in form.getlist("ids")]
            for file_id in ids:
                record = session.get(m.MediaFile, file_id)
                if record is None:
                    continue
                result = delete_file(session, record, dry_run=dry_run)
                to_trash += bool(result.trashed)
                if result.deleted:
                    deleted += 1
                elif not dry_run:
                    skipped.append(result.message)
        if dry_run:
            msg = (
                f"Preview: {len(ids)} file(s) would be deleted "
                f"({to_trash} copies moved to the Trash)"
            )
        else:
            msg = f"Deleted {deleted} file(s): the copies in processed-files went to the Trash"
            if skipped:
                msg += f"; {len(skipped)} not deleted ({skipped[0]})"
        notify(request)
        back = re.sub(r"[?&](msg|live)=[^&]*", "", form.get("back") or "/")
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}msg={msg}", status_code=303)

    @app.post("/details/{file_id}/delete")
    def delete_one(request: Request, file_id: int):
        from mam.helpers.file_organizer.trash import delete_file

        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            result = delete_file(session, record, dry_run=False)
        if not result.deleted:
            return RedirectResponse(f"/details/{file_id}?error={result.message}", status_code=303)
        notify(request)
        return RedirectResponse(
            f"/?msg=File deleted: {result.message}. The original was not touched.", status_code=303
        )

    # ----------------------------------------------------------- file detail
    @app.get("/files/{file_id}")
    def file_page_old(file_id: int, request: Request):
        query = request.url.query
        return RedirectResponse(f"/details/{file_id}" + (f"?{query}" if query else ""), 303)

    def _verse_links(record: m.MediaFile) -> list[dict[str, str]]:
        """The vedabase.io pages of the verse the class discoverer heard or the filename has,
        shown once the class discoverer has processed the file."""
        from mam.config import get_settings
        from mam.helpers.vedabase import VerseRef
        from mam.tools.class_discoverer.tool import filename_verse

        cd = (record.meta or {}).get("class_discoverer")
        if not cd:
            return []
        base = get_settings().vedabase_url.rstrip("/")
        refs: list[tuple[str, VerseRef]] = []
        heard = VerseRef.from_what((cd.get("found") or {}).get("what"))
        if heard is not None:
            refs.append(("heard in the recording", heard))
        named = filename_verse(record)
        if named is not None and (heard is None or named.what != heard.what):
            refs.append(("in the filename", named))
        links = []
        for label, ref in refs:
            for verse in (ref.verses()[0], ref.verses()[-1]) if ref.range_end else (ref,):
                links.append({"text": f"{verse.readable} ({label})", "url": base + verse.path})
        return links

    @app.get("/details/{file_id}", response_class=HTMLResponse)
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
            # A pending Renamer review turns the page into the review page: the same form with
            # Approve / Reject / Exclude, plus the reasons. Other tools' reviews keep their page.
            pending = session.scalars(
                select(m.Review)
                .where(m.Review.media_file_id == file_id, m.Review.outcome == "pending")
                .order_by(m.Review.id)
            ).all()
            review = pending[0] if pending else None
            review_id = review.id if review else None
            review_tool = review.tool if review else None
            proposal = dict(review.proposal) if review else None
            splitter = {}
            if review and review.tool == "kirtan-splitter":
                from mam.tools.kirtan_splitter.phrases import mantra_choices
                from mam.tools.kirtan_splitter.tool import format_time
                from mam.tools.renamer.knowledge import reference_if_ready

                ref = reference_if_ready()

                splitter = {
                    "region_colors": _region_colors(record, proposal),
                    "mantras": mantra_choices(ref.matcher if ref else None),
                    "split_text": format_time(proposal["split_at"])
                    if proposal.get("split_at") is not None
                    else "",
                    "class_text": format_time(proposal["class_start"])
                    if proposal.get("class_start") is not None
                    else "",
                }
            verse_links = _verse_links(record)
            from mam.tools.content_discoverer.tool import stored_results

            content_results = stored_results(record)
            next_id = (
                session.scalar(
                    select(m.Review.media_file_id)
                    .where(m.Review.outcome == "pending", m.Review.media_file_id != file_id)
                    .order_by(m.Review.id)
                )
                if review
                else None
            )
        return render(
            request,
            "file.html",
            menu=menu_tools(),
            mode="review"
            if review_tool
            and review_tool not in ("kirtan-splitter", "class-discoverer", "content-discoverer")
            else "file",
            review=review,
            review_id=review_id,
            review_tool=review_tool,
            verse_links=verse_links,
            content_results=content_results,
            p=proposal,
            next_id=next_id,
            **splitter,
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
            return HTMLResponse("", headers={"HX-Redirect": f"/details/{file_id}"})
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

    def _waveform_source(file_id: int) -> Path:
        """The audio the splitter works on: the extracted audio of a video, else the copy."""
        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            meta_audio = ((record.meta or {}).get("splitter") or {}).get("audio")
            candidates = [meta_audio, record.processed_path, record.original_path]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return Path(candidate)
        raise HTTPException(404, "File not available")

    @app.get("/files/{file_id}/waveform/peaks")
    def waveform_peaks(file_id: int):
        """Peaks for the waveform editor (made once, then kept); also readies the audio preview."""
        from mam.manager import waveform

        source = _waveform_source(file_id)
        try:
            data = waveform.peaks_for(source, get_settings())
            waveform.playable(source, get_settings())
        except MamError as exc:
            raise HTTPException(500, str(exc)) from exc
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/files/{file_id}/waveform/audio")
    def waveform_audio(file_id: int):
        """The recording as a browser can play it (a small mp3 for WMA and video)."""
        from mam.manager import waveform

        try:
            return FileResponse(waveform.playable(_waveform_source(file_id), get_settings()))
        except MamError as exc:
            raise HTTPException(500, str(exc)) from exc

    # ------------------------------------------------------------------ jobs
    @app.get("/nav/resources", response_class=HTMLResponse)
    def nav_resources(request: Request):
        """CPU, GPU, memory and temperature bars of the header (see _resources.html)."""
        from mam.manager.resources import level, snapshot

        data = snapshot()
        bars = [
            ("CPU", "cpu", data["cpu"], "%", "CPU"),
            ("GPU", "gpu", data["gpu"], "%", "GPU"),
            ("Memory", "memory", data["memory"], "%", "MEM"),
            ("CPU temperature", "temp", data["temp"], "\u00b0C", "TEMP"),
        ]
        shown = [
            {
                "label": label,
                "icon": icon,
                "value": round(v),
                "unit": unit,
                "color": level(kind, v),
                "width": min(100, max(2, round(v))),
            }
            for label, kind, v, unit, icon in bars
            if v is not None
        ]
        return render(request, "_resources.html", bars=shown)

    @app.get("/nav/badges", response_class=HTMLResponse)
    def nav_badges(request: Request):
        """Navbar counts, swapped out of band while jobs run (see _nav_badges.html)."""
        return render(request, "_nav_badges.html", oob=True)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return render(request, "jobs.html", **_jobs_context(request))

    @app.get("/jobs/activity", response_class=HTMLResponse)
    def jobs_activity(request: Request):
        """Polled by the live message: which tool is doing what."""
        with session_scope() as session:
            data = queries.activity(session)
        return render(request, "_activity.html", activity=data)

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

    def _stop(request: Request, file_id: int | None = None) -> tuple[int, int]:
        queue: JobQueue | None = getattr(request.app.state, "queue", None)
        if queue is None:
            queue = JobQueue(settings)  # no workers here (tests): queued jobs can still be stopped
        return queue.cancel_jobs(file_id)

    @app.post("/jobs/cancel")
    def cancel_queued(request: Request):
        """Stop all processing (the navbar, the live banner and the Jobs page)."""
        _stop(request)
        if request.headers.get("HX-Request"):
            return Response(status_code=204)
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/files/{file_id}/stop")
    def stop_file(request: Request, file_id: int):
        """Stop the queued and running jobs of one file (the Stop icon in the file list)."""
        _stop(request, file_id)
        if request.headers.get("HX-Request"):
            return Response(status_code=204)
        return RedirectResponse(f"/details/{file_id}", status_code=303)

    # ---------------------------------------------------------------- import
    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request, root: int = 0, path: str = ""):
        roots = settings.import_roots
        if not 0 <= root < len(roots):
            raise HTTPException(404)
        label, base = roots[root]
        current = _safe_join(base, path)
        folders, files = [], []
        if current.is_dir():
            for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    folders.append(child.name)
                elif media_kind(child):
                    files.append(
                        {
                            "name": child.name,
                            "kind": media_kind(child),
                            "size": child.stat().st_size,
                        }
                    )
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
            files=files,
            crumbs=crumbs,
        )

    @app.post("/import")
    def import_folder(
        request: Request,
        root: Annotated[int, Form()],
        path: Annotated[str, Form()] = "",
        items: Annotated[list[str] | None, Form()] = None,
        all: Annotated[str, Form()] = "",
    ):
        """Import the ticked folders and files; the whole current folder with `all` or no ticks."""
        _, base = settings.import_roots[root]
        folder = _safe_join(base, path)
        targets = [_safe_join(folder, name) for name in ([] if all else items or [])] or [folder]
        with session_scope() as session:
            for target in targets:
                enqueue(session, "scan", dry_run=False, params={"path": str(target)})
        notify(request)
        label = targets[0].name or str(targets[0])
        if len(targets) > 1:
            label = f"{len(targets)} selected items"
        return RedirectResponse(f"/jobs?msg=Scanning {label}&live=1", status_code=303)

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
    def place_country_status() -> dict:
        """Place options without a country in the Baserow place_country table (Settings)."""
        from mam.tools.renamer.knowledge import norm_key, reference_if_ready

        ref = reference_if_ready()
        if ref is None:
            return {"ready": False}
        general = {norm_key(p) for p in ref.aliases.get("no_country") or []}

        def is_general(name: str) -> bool:
            """A general place, or a spelling that a general place lists as its alias."""
            target = ref.places.get(norm_key(name))
            return norm_key(name) in general or bool(target and norm_key(target.name) in general)

        missing = sorted(
            {
                name
                for name in ref.place_options
                if not is_general(name) and not (ref.place_rows.get(norm_key(name)) or {}).get("cc")
            },
            key=str.casefold,
        )
        return {
            "ready": True,
            "rows": len(ref.place_rows),
            "missing": missing,
            "problems": ref.place_country_problems,
        }

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        from mam.manager.maintenance import summary

        return render(
            request,
            "settings.html",
            info=summary(settings),
            places=place_country_status(),
            status_choices=queries.status_choices(),
            default_status=default_status_setting(),
            default_size=default_size_setting(),
            page_sizes=queries.PAGE_SIZES,
            renamer_move_files=renamer_move_files(),
            renamer_postfix=renamer_postfix(),
            speech_only=transcribe_speech_only(settings.transcribe_speech_only),
            option_groups=option_groups(),
            values={o.key: option_value(o, settings) for o in OPTIONS},
        )

    def saved(request: Request, what: str, error: str | None = None):
        """The little status next to an autosaved setting: a check, or the problem."""
        return templates.TemplateResponse(
            request, "_setting_saved.html", {"what": what, "error": error}
        )

    @app.post("/settings/renamer/move")
    async def settings_renamer_move(request: Request):
        form = await request.form()
        set_renamer_options(form.get("move_files") == "on", renamer_postfix())
        return saved(request, "Move files instead of copying")

    @app.post("/settings/option/{key}")
    async def settings_option(request: Request, key: str):
        option = OPTION_BY_KEY.get(key)
        if option is None:
            raise HTTPException(404)
        form = await request.form()
        try:
            set_option(key, form.get("value"))
        except ValueError as error:
            return saved(request, option.label, str(error))
        return saved(request, option.label)

    @app.post("/settings/class-discoverer/speech-only")
    async def settings_speech_only(request: Request):
        form = await request.form()
        set_transcribe_speech_only(form.get("speech_only") == "on")
        return saved(request, "Only transcribe speech")

    @app.post("/settings/renamer/postfix")
    async def settings_renamer_postfix(request: Request):
        form = await request.form()
        try:
            set_renamer_options(renamer_move_files(), str(form.get("postfix") or ""))
        except ValueError as error:
            return saved(request, "Postfix", str(error))
        return saved(request, "Postfix")

    @app.post("/settings/default-status")
    async def settings_default_status(request: Request):
        form = await request.form()
        value = str(form.get("default_status") or "")
        if value not in {v for v, _, _ in queries.status_choices()}:
            return saved(request, "Default Status filter", "Unknown status")
        set_default_status(value)
        return saved(request, "Default Status filter")

    @app.post("/settings/default-size")
    async def settings_default_size(request: Request):
        form = await request.form()
        try:
            value = int(str(form.get("default_size") or ""))
        except ValueError:
            value = 0
        if value not in queries.PAGE_SIZES:
            return saved(request, "Default Show", "Unknown number of files")
        set_default_size(value)
        return saved(request, "Default Show")

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
            old_backups=form.get("old_backups") == "on",
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
        if options.old_backups:
            msg += f" ({result.backups_deleted} earlier backup(s) deleted)"
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
