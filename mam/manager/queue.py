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
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope
from mam.helpers.local_db.repo import set_status
from mam.manager.tools import TOOLS, JobContext, ToolOutcome, next_in_pipeline

log = logging.getLogger(__name__)

POOLS = ("ml", "cpu", "io")


def now() -> datetime:
    return datetime.now(UTC)


def status_for(label: str, state: str) -> str:
    return {
        "queued": f"Queued {label}",
        "running": f"Processing {label}",
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
) -> int:
    """Queue `tool` for each file (or once, for folder-level tools). Returns the job count."""
    spec = TOOLS[tool]
    count = 0
    if not spec.per_file:
        session.add(m.Job(tool=tool, pool=spec.pool, dry_run=dry_run, params=params))
        return 1
    for file_id in file_ids:
        record = session.get(m.MediaFile, file_id)
        if record is None or record.status in ("Excluded",):
            continue
        busy = session.scalar(
            select(m.Job.id).where(
                m.Job.media_file_id == file_id,
                m.Job.tool == tool,
                m.Job.state.in_(("queued", "running")),
            )
        )
        if busy:
            continue
        session.add(
            m.Job(
                media_file_id=file_id,
                tool=tool,
                pool=spec.pool,
                dry_run=dry_run,
                pipeline=pipeline,
                params=params,
            )
        )
        if spec.sets_status and not dry_run:
            set_status(session, record, status_for(spec.label, "queued"), tool=tool)
        count += 1
    return count


class Worker(threading.Thread):
    def __init__(self, queue: JobQueue, pool: str, index: int) -> None:
        super().__init__(name=f"mam-{pool}-{index}", daemon=True)
        self.queue = queue
        self.pool = pool

    def run(self) -> None:
        while not self.queue.stopping.is_set():
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

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with session_scope() as session:
            session.execute(
                update(m.Job)
                .where(m.Job.state == "running")
                .values(state="queued", started_at=None)
            )
        sizes = {
            "ml": self.settings.workers_ml,
            "cpu": self.settings.workers_cpu,
            "io": self.settings.workers_io,
        }
        for pool, size in sizes.items():
            for i in range(max(1, size)):
                worker = Worker(self, pool, i)
                worker.start()
                self.workers.append(worker)
        watcher = threading.Thread(target=self._watch_inbox, name="mam-inbox", daemon=True)
        watcher.start()
        log.info("Job queue started: %s", sizes)

    def stop(self, timeout: float = 5.0) -> None:
        self.stopping.set()
        self.wakeup.set()
        deadline = time.monotonic() + timeout
        for worker in self.workers:
            worker.join(max(0.0, deadline - time.monotonic()))

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
                .values(state="running", started_at=now(), attempts=m.Job.attempts + 1)
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
                ctx = JobContext(job_id, session, self.settings, dry_run, params, record)
                outcome = spec.run(ctx)
                self._finish(session, job_id, record, outcome)
        except Exception as exc:
            log.exception("Job %s (%s) failed", job_id, tool)
            with session_scope() as session:
                job = session.get(m.Job, job_id)
                job.state, job.finished_at = "failed", now()
                job.error = f"{exc}\n\n{traceback.format_exc()[-3000:]}"
                if job.media_file_id and spec.sets_status and not dry_run:
                    record = session.get(m.MediaFile, job.media_file_id)
                    set_status(session, record, status_for(spec.label, "failed"), tool, str(exc))
            return

        if pipeline and not dry_run and outcome.state == "done":
            following = next_in_pipeline(tool)
            if following:
                with session_scope() as session:
                    enqueue(session, following, [job.media_file_id], dry_run=False, pipeline=True)
                self.notify()

    def _finish(
        self, session: Session, job_id: int, record: m.MediaFile | None, outcome: ToolOutcome
    ) -> None:
        job = session.get(m.Job, job_id)
        spec = TOOLS[job.tool]
        job.state = "done"
        job.finished_at = now()
        job.result = {"state": outcome.state, "summary": outcome.summary, **outcome.data}
        if record is None or job.dry_run:
            return
        if outcome.state == "corrupted":
            set_status(session, record, "Corrupted", job.tool, outcome.summary)
        elif spec.sets_status and outcome.state in ("done", "review", "skipped"):
            set_status(
                session, record, status_for(spec.label, outcome.state), job.tool, outcome.summary
            )
        elif spec.sets_status and record.status.startswith(("Queued ", "Processing ")):
            # The tool changed nothing (e.g. kept an approved name): restore the earlier status.
            set_status(session, record, stable_status(session, record), job.tool, outcome.summary)


def stable_status(session: Session, record: m.MediaFile) -> str:
    """The last status that was not 'Queued ...' or 'Processing ...'."""
    for status in session.scalars(
        select(m.StatusHistory.new_status)
        .where(m.StatusHistory.media_file_id == record.id)
        .order_by(m.StatusHistory.id.desc())
    ):
        if not status.startswith(("Queued ", "Processing ")):
            return status
    return "Not processed"


def resume_after_review(session: Session, record: m.MediaFile, tool: str) -> None:
    """After a review is approved, continue the pipeline at the next tool."""
    following = next_in_pipeline(tool)
    if following:
        enqueue(session, following, [record.id], dry_run=False, pipeline=True)
