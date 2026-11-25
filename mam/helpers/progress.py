"""Live progress of the running job (SPEC.md section 8).

The job queue installs a reporter for the thread that runs a job; any tool or helper below it
calls `report("Transcribing, part 3 of 14", 0.2)` without knowing about jobs. Outside a job
(the command line, tests) `report` does nothing. A report is also the safe point where a job
the operator stopped ends: the reporter raises `JobCancelled` there (queue.py).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from mam.errors import JobCancelled

Reporter = Callable[[str, float | None], None]

_reporter: ContextVar[Reporter | None] = ContextVar("mam_progress_reporter", default=None)


def report(step: str, fraction: float | None = None, *, safe_to_stop: bool = True) -> None:
    """Say what the job is doing now; `fraction` (0 to 1) is how far the whole job is.

    Pass `safe_to_stop=False` for a step that comes after files were changed: a stopped job
    must finish there, so that the change is recorded.
    """
    reporter = _reporter.get()
    if reporter is None:
        return
    with contextlib.nullcontext() if safe_to_stop else uninterruptible():
        reporter(step, None if fraction is None else max(0.0, min(1.0, fraction)))


@contextmanager
def reporting(reporter: Reporter) -> Iterator[None]:
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


# ---------------------------------------------------------------- stopping a running job
# The operator's Stop button asks a running job to end. Work that waits on an outside program
# (ffmpeg, whisper, the transcription child process) registers how to kill it with `killable`,
# so Stop does not have to wait for the step to finish. The program is killed, the job ends with
# `JobCancelled`, and every writer works on a temporary file first (renamed only when complete),
# so no half-written file is left behind.

_job_id: ContextVar[int | None] = ContextVar("mam_job_id", default=None)
_cancelled: set[int] = set()
_kill_hooks: dict[int, set[Callable[[], None]]] = {}
_lock = threading.Lock()


@contextmanager
def bound_job(job_id: int) -> Iterator[None]:
    """Mark the current thread as running job `job_id` (the queue does this)."""
    token = _job_id.set(job_id)
    try:
        yield
    finally:
        _job_id.reset(token)


def request_cancel(job_id: int) -> None:
    """Ask job `job_id` to stop and kill the outside programs it is waiting for."""
    with _lock:
        _cancelled.add(job_id)
        hooks = list(_kill_hooks.get(job_id, ()))
    for hook in hooks:
        with contextlib.suppress(Exception):  # the program may already be gone
            hook()


def is_cancelled(job_id: int) -> bool:
    return job_id in _cancelled


def clear_cancel(job_id: int) -> None:
    with _lock:
        _cancelled.discard(job_id)
        _kill_hooks.pop(job_id, None)


_protected: ContextVar[bool] = ContextVar("mam_job_protected", default=False)


@contextmanager
def uninterruptible() -> Iterator[None]:
    """A stretch of work that must finish once begun (the bookkeeping after a file was
    written): Stop does not interrupt it, and nothing in it is killed."""
    token = _protected.set(True)
    try:
        yield
    finally:
        _protected.reset(token)


def is_protected() -> bool:
    """Inside a stretch of work that a stopped job must finish."""
    return _protected.get()


def check_cancelled() -> None:
    """Raise `JobCancelled` when the running job was stopped (does nothing outside a job)."""
    job_id = _job_id.get()
    if _protected.get():
        return
    if job_id is not None and job_id in _cancelled:
        raise JobCancelled("Stopped by the operator")


def kill_on_stop(kill: Callable[[], None]) -> None:
    """Like `killable`, without a block: `kill` stays registered until the job ends (killing a
    program that has already finished does nothing). Pair it with `check_cancelled()`."""
    job_id = _job_id.get()
    if job_id is None or _protected.get():
        return
    with _lock:
        _kill_hooks.setdefault(job_id, set()).add(kill)
        already = job_id in _cancelled
    if already:
        kill()


@contextmanager
def killable(kill: Callable[[], None]) -> Iterator[None]:
    """While the block runs, Stop calls `kill` (it must end the program the block waits for).

    When the job was stopped, leaving the block raises `JobCancelled`, so a program that was
    killed never passes for one that failed or finished.
    """
    job_id = _job_id.get()
    if job_id is None or _protected.get():
        yield
        return
    with _lock:
        _kill_hooks.setdefault(job_id, set()).add(kill)
        already = job_id in _cancelled
    try:
        if already:
            kill()
        yield
    finally:
        with _lock:
            _kill_hooks.get(job_id, set()).discard(kill)
    check_cancelled()
