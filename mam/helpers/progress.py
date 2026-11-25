"""Live progress of the running job (SPEC.md section 8).

The job queue installs a reporter for the thread that runs a job; any tool or helper below it
calls `report("Transcribing, part 3 of 14", 0.2)` without knowing about jobs. Outside a job
(the command line, tests) `report` does nothing. A report is also the safe point where a job
the operator stopped ends: the reporter raises `JobCancelled` there (queue.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

Reporter = Callable[[str, float | None], None]

_reporter: ContextVar[Reporter | None] = ContextVar("mam_progress_reporter", default=None)


def report(step: str, fraction: float | None = None) -> None:
    """Say what the job is doing now; `fraction` (0 to 1) is how far the whole job is."""
    reporter = _reporter.get()
    if reporter is not None:
        reporter(step, None if fraction is None else max(0.0, min(1.0, fraction)))


@contextmanager
def reporting(reporter: Reporter) -> Iterator[None]:
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)
