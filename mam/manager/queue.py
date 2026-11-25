"""Persisted job queue with one worker pool per resource (SPEC.md section 1).

Jobs live in the local DB, so the queue survives restarts: jobs that were running when the
process stopped are put back in the queue on start.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import JobCancelled
from mam.helpers.local_db import models as m
from mam.helpers.local_db.app_settings import tuned
from mam.helpers.local_db.db import session_scope
from mam.helpers.local_db.repo import set_status
from mam.helpers.progress import reporting
from mam.manager.tools import PIPELINE_ORDER, TOOLS, JobContext, ToolOutcome, next_in_pipeline
from mam.statuses import PROCESSING, QUEUED, is_busy

log = logging.getLogger(__name__)

POOLS = ("ml", "cpu", "io")


def now() -> datetime:
    return datetime.now(UTC)


def status_for(label: str, state: str) -> str:
    return {
        "queued": QUEUED,
        "running": PROCESSING,
        "done": f"{label} Done",
        "review": f"{label} Review",
        "failed": f"{label} Failed",
        "skipped": "Skipped",
        "corrupted": "Corrupted",
    }[state]


def enqueue(
    session: Session,
    tool: str,
    file_ids: Iterable[int] = (),
    *,
    dry_run: bool = True,
    params: dict[str, Any] | None = None,
    pipeline: bool = False,
    waiting: list[str] | None = None,
) -> int:
    """Queue `tool` for each file (or once, for folder-level tools). Returns the job count.

    A file whose earlier pipeline tool still has a review open is not queued: it waits for that
    review (SPEC.md section 4; a dry run is not blocked). The earlier tool's label is added to
    `waiting` for each such file."""
    spec = TOOLS[tool]
    count = 0
    if not spec.per_file:
        session.add(m.Job(tool=tool, pool=spec.pool, dry_run=dry_run, params=params))
        return 1
    run, run_params = spec, params
    if spec.first_step and not (params or {}).get("loudness_done"):
        # the tool starts with a step on another pool (loudness); that step queues the tool
        run = TOOLS[spec.first_step]
        run_params = {**(params or {}), "then": tool, "then_pipeline": pipeline}
    for file_id in file_ids:
        record = session.get(m.MediaFile, file_id)
        if record is None or record.status in ("Excluded", "Deleted"):
            continue
        blocker = None if dry_run else review_blocking(session, file_id, tool)  # a preview is fine
        if blocker:
            if waiting is not None:
                waiting.append(blocker)
            continue
        busy = session.scalar(
            select(m.Job.id).where(
                m.Job.media_file_id == file_id,
                m.Job.tool.in_((tool, run.name)),
                m.Job.state.in_(("queued", "running")),
            )
        )
        if busy:
            continue
        session.add(
            m.Job(
                media_file_id=file_id,
                tool=run.name,
                pool=run.pool,
                dry_run=dry_run,
                pipeline=pipeline,
                params=run_params,
            )
        )
        if spec.sets_status and not dry_run:
            set_status(session, record, status_for(spec.label, "queued"), tool=tool)
        count += 1
    return count


def review_blocking(session: Session, file_id: int, tool: str) -> str | None:
    """The label of an earlier pipeline tool whose review of this file is still open, if any.
    Only tools after the first one wait; a tool outside the pipeline never does."""
    if tool not in PIPELINE_ORDER or PIPELINE_ORDER.index(tool) == 0:
        return None
    earlier = PIPELINE_ORDER[: PIPELINE_ORDER.index(tool)]
    open_tool = session.scalar(
        select(m.Review.tool)
        .where(
            m.Review.media_file_id == file_id,
            m.Review.outcome == "pending",
            m.Review.tool.in_(earlier),
        )
        .limit(1)
    )
    return TOOLS[open_tool].label if open_tool in TOOLS else open_tool


class Worker(threading.Thread):
    def __init__(self, queue: JobQueue, pool: str, index: int) -> None:
        super().__init__(name=f"mam-{pool}-{index}", daemon=True)
        self.queue = queue
        self.pool = pool
        self.index = index

    def run(self) -> None:
        while not self.queue.stopping.is_set():
            if self.index >= self.queue.limits.get(self.pool, 1 << 30):
                self.queue.wakeup.wait(1.0)  # the pool is scaled down: this worker rests
                continue
            try:
                job_id = self.queue.claim(self.pool)
            except Exception:  # database busy etc.; try again shortly
                log.exception("Could not claim a job")
                job_id = None
            if job_id is None:
                self.queue.wakeup.wait(1.0)
                continue
            self.queue.execute(job_id)


class JobQueue:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.workers: list[Worker] = []
        self._claim_lock = threading.Lock()
        self.limits: dict[str, int] = {}  # workers allowed to run now, per scaled pool
        self.maximums: dict[str, int] = {}
        self.stop_requested: set[int] = set()  # running jobs the operator stopped

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with session_scope() as session:
            session.execute(
                update(m.Job)
                .where(m.Job.state == "running")
                .values(state="queued", started_at=None, step=None, progress=None)
            )
        pool_settings = tuned(self.settings)
        sizes = {
            "ml": pool_settings.workers_ml,
            "cpu": pool_settings.workers_cpu,
            "io": pool_settings.workers_io,
        }
        for pool, size in sizes.items():
            for i in range(max(1, size)):
                worker = Worker(self, pool, i)
                worker.start()
                self.workers.append(worker)
        if pool_settings.autoscale_workers:
            self.maximums = {"cpu": max(1, sizes["cpu"]), "io": max(1, sizes["io"])}
            self.limits = {pool: min(2, size) for pool, size in self.maximums.items()}
            scaler = threading.Thread(target=self._autoscale, name="mam-autoscale", daemon=True)
            scaler.start()
        watcher = threading.Thread(target=self._watch_inbox, name="mam-inbox", daemon=True)
        watcher.start()
        log.info("Job queue started: %s", sizes)

    def stop(self, timeout: float = 5.0) -> None:
        self.stopping.set()
        self.wakeup.set()
        deadline = time.monotonic() + timeout
        for worker in self.workers:
            worker.join(max(0.0, deadline - time.monotonic()))

    def _autoscale(self, interval: float = 5.0) -> None:
        """Every few seconds add a worker while the computer has spare CPU and memory and jobs
        wait, and take one away when it is busy (resources.py)."""
        from mam.manager.resources import next_limit, sample

        sample()  # the first reading has no interval to measure
        while not self.stopping.wait(interval):
            try:
                cpu, free = sample()
                with session_scope() as session:
                    for pool, maximum in self.maximums.items():
                        waiting = bool(
                            session.scalar(
                                select(m.Job.id)
                                .where(m.Job.pool == pool, m.Job.state == "queued")
                                .limit(1)
                            )
                        )
                        new = next_limit(self.limits[pool], maximum, cpu, free, waiting)
                        if new != self.limits[pool]:
                            log.info(
                                "%s workers %s -> %s (CPU %.0f%%, memory free %.0f%%)",
                                pool,
                                self.limits[pool],
                                new,
                                cpu,
                                free,
                            )
                            self.limits[pool] = new
            except Exception:
                log.exception("Autoscaling failed")

    def _watch_inbox(self, interval: float = 60.0) -> None:
        """Register new files dropped into the inbox folder (SPEC.md section 2)."""
        from mam.helpers.local_db.repo import media_kind

        inbox = self.settings.inbox_path
        while not self.stopping.wait(interval):
            try:
                if not inbox.is_dir():
                    continue
                files = {
                    str(p.resolve()) for p in inbox.rglob("*") if p.is_file() and media_kind(p)
                }
                if not files:
                    continue
                with session_scope() as session:
                    known = set(
                        session.scalars(
                            select(m.MediaFile.original_path).where(
                                m.MediaFile.original_path.in_(files)
                            )
                        )
                    ) | set(
                        session.scalars(select(m.FileAlias.path).where(m.FileAlias.path.in_(files)))
                    )
                    pending = session.scalar(
                        select(m.Job.id).where(
                            m.Job.tool == "scan", m.Job.state.in_(("queued", "running"))
                        )
                    )
                    if files - known and not pending:
                        enqueue(session, "scan", dry_run=False, params={"path": str(inbox)})
                        self.notify()
            except Exception:
                log.exception("Inbox watcher failed")

    def notify(self) -> None:
        self.wakeup.set()
        self.wakeup.clear()

    def cancel_jobs(self, file_id: int | None = None) -> tuple[int, int]:
        """Stop processing: all jobs, or only those of one file (the Stop buttons).

        Queued jobs are cancelled at once. A running job cannot be killed safely, so it is asked
        to stop and ends at its next report (the start of its next step); a step that is
        already running finishes first, so no file is left half written. Returns
        (queued jobs cancelled, running jobs asked to stop).
        """
        with session_scope() as session:
            where = [m.Job.state.in_(("queued", "running"))]
            if file_id is not None:
                where.append(m.Job.media_file_id == file_id)
            cancelled = asking = 0
            for job in session.scalars(select(m.Job).where(*where)):
                if job.state == "queued":
                    job.state, job.finished_at = "cancelled", now()
                    _restore_status(session, job)
                    cancelled += 1
                else:
                    self.stop_requested.add(job.id)
                    job.step = "Stopping"
                    asking += 1
        return cancelled, asking

    # ----------------------------------------------------------------- jobs
    def claim(self, pool: str) -> int | None:
        with self._claim_lock, session_scope() as session:
            job_id = session.scalar(
                select(m.Job.id)
                .where(m.Job.pool == pool, m.Job.state == "queued")
                .order_by(m.Job.id)
                .limit(1)
            )
            if job_id is None:
                return None
            session.execute(
                update(m.Job)
                .where(m.Job.id == job_id)
                .values(
                    state="running",
                    started_at=now(),
                    attempts=m.Job.attempts + 1,
                    step="Starting",
                    progress=None,
                )
            )
            return job_id

    def execute(self, job_id: int) -> None:
        with session_scope() as session:
            job = session.get(m.Job, job_id)
            spec = TOOLS[job.tool]
            record = session.get(m.MediaFile, job.media_file_id) if job.media_file_id else None
            if record is not None and spec.sets_status and not job.dry_run:
                set_status(session, record, status_for(spec.label, "running"), tool=job.tool)
            dry_run, params, pipeline, tool = job.dry_run, job.params or {}, job.pipeline, job.tool

        try:
            with session_scope() as session:
                record = session.get(m.MediaFile, job.media_file_id) if job.media_file_id else None
                ctx = JobContext(job_id, session, tuned(self.settings), dry_run, params, record)
                with reporting(self._reporter(job_id)):
                    outcome = spec.run(ctx)
                self._finish(session, job_id, record, outcome)
        except JobCancelled:
            log.info("Job %s (%s) stopped by the operator", job_id, tool)
            self.stop_requested.discard(job_id)
            with session_scope() as session:
                job = session.get(m.Job, job_id)
                job.state, job.finished_at = "cancelled", now()
                job.step = job.progress = None
                _restore_status(session, job)
                # a step that queued its follower before it was stopped
                self._cancel_followers(session, job)
            return
        except Exception as exc:
            self.stop_requested.discard(job_id)
            log.exception("Job %s (%s) failed", job_id, tool)
            with session_scope() as session:
                job = session.get(m.Job, job_id)
                job.state, job.finished_at = "failed", now()
                job.step = job.progress = None
                job.error = f"{exc}\n\n{traceback.format_exc()[-3000:]}"
                if job.media_file_id and spec.sets_status and not dry_run:
                    record = session.get(m.MediaFile, job.media_file_id)
                    set_status(session, record, status_for(spec.label, "failed"), tool, str(exc))
            return

        if job_id in self.stop_requested:
            # stopped, but the job had no step left to stop at: it finished, nothing follows
            self.stop_requested.discard(job_id)
            with session_scope() as session:
                self._cancel_followers(session, session.get(m.Job, job_id))
            return

        if (
            pipeline
            and not dry_run
            and (outcome.state == "done" or outcome.data.get("continue_pipeline"))
        ):
            following = next_in_pipeline(tool)
            if following:
                with session_scope() as session:
                    enqueue(session, following, [job.media_file_id], dry_run=False, pipeline=True)
                self.notify()

    def _cancel_followers(self, session: Session, job: m.Job) -> None:
        """Cancel the queued jobs the stopped job's file still has."""
        if job.media_file_id is None:
            return
        for follower in session.scalars(
            select(m.Job).where(m.Job.media_file_id == job.media_file_id, m.Job.state == "queued")
        ):
            follower.state, follower.finished_at = "cancelled", now()
            _restore_status(session, follower)

    def _reporter(self, job_id: int):
        """Writes what the running job is doing to the job row, a few times a second at most.

        It uses its own short transaction and never fails the job: a busy database only means
        one update is skipped.
        """
        last = [0.0]

        def write(step: str, fraction: float | None) -> None:
            if job_id in self.stop_requested:
                raise JobCancelled("Stopped by the operator")
            moment = time.monotonic()
            if moment - last[0] < 0.3:
                return
            last[0] = moment
            try:
                with session_scope() as session:
                    session.execute(
                        update(m.Job)
                        .where(m.Job.id == job_id, m.Job.state == "running")
                        .values(step=step[:200], progress=fraction)
                    )
            except Exception:
                log.debug("Could not store progress of job %s", job_id, exc_info=True)

        return write

    def _finish(
        self, session: Session, job_id: int, record: m.MediaFile | None, outcome: ToolOutcome
    ) -> None:
        job = session.get(m.Job, job_id)
        spec = TOOLS[job.tool]
        job.state = "done"
        job.finished_at = now()
        job.step = job.progress = None
        job.result = {"state": outcome.state, "summary": outcome.summary, **outcome.data}
        if record is None or job.dry_run:
            return
        if outcome.state == "corrupted":
            set_status(session, record, "Corrupted", job.tool, outcome.summary)
        elif spec.sets_status and outcome.status:
            set_status(session, record, outcome.status, job.tool, outcome.summary)
        elif (
            spec.sets_status
            and (job.params or {}).get("keep_status")
            and outcome.state in ("done", "info")
        ):
            # A follow-up job another tool queued (e.g. the Renamer moving a kirtan recording):
            # the file keeps the status that tool gave it.
            set_status(session, record, stable_status(session, record), job.tool, outcome.summary)
        elif spec.sets_status and outcome.state in ("done", "review", "skipped"):
            set_status(
                session, record, status_for(spec.label, outcome.state), job.tool, outcome.summary
            )
        elif spec.sets_status and is_busy(record.status):
            # The tool changed nothing (e.g. kept an approved name): restore the earlier status.
            set_status(session, record, stable_status(session, record), job.tool, outcome.summary)


def _restore_status(session: Session, job: m.Job) -> None:
    """Give the file the status it had before a stopped job queued or started it."""
    if not job.media_file_id or job.dry_run or job.tool not in TOOLS:
        return
    if not TOOLS[job.tool].sets_status:
        return
    record = session.get(m.MediaFile, job.media_file_id)
    if record is not None and is_busy(record.status):
        set_status(session, record, stable_status(session, record), job.tool, "stopped")


def stable_status(session: Session, record: m.MediaFile) -> str:
    """The last status that was not 'Queued' or 'Processing'."""
    for status in session.scalars(
        select(m.StatusHistory.new_status)
        .where(m.StatusHistory.media_file_id == record.id)
        .order_by(m.StatusHistory.id.desc())
    ):
        if not is_busy(status):
            return status
    return "Not processed"


def resume_after_review(session: Session, record: m.MediaFile, tool: str) -> str | None:
    """After a review is approved, continue the pipeline at the next tool.

    Returns the label of the tool that was queued, or None when there is none to run.
    """
    following = next_in_pipeline(tool)
    if following and enqueue(session, following, [record.id], dry_run=False, pipeline=True):
        return TOOLS[following].label
    return None
