"""File status names (SPEC.md section 7).

`Queued` and `Processing` carry no tool name (decided 2026-11-23): the file list shows which
tool in its job column. The Kirtan splitter has three finished statuses of its own that say
what it found, instead of a bare `Skipped`.
"""

from __future__ import annotations

QUEUED = "Queued"
PROCESSING = "Processing"
SPLITTER_CLASS = "Kirtan splitter: Class"  # no kirtan before the class: nothing to split
SPLITTER_KIRTAN = "Kirtan splitter: Kirtan"  # singing throughout: a kirtan recording
SPLITTER_SPLIT = "Kirtan splitter: Split"  # cut into a kirtan and a class
SPLITTER_NOT_SPLIT = "Kirtan splitter: Not split"  # a reviewer decided not to split
SPLITTER_STATUSES = (SPLITTER_CLASS, SPLITTER_KIRTAN, SPLITTER_SPLIT, SPLITTER_NOT_SPLIT)


def is_queued(status: str) -> bool:
    """`Queued` (and the older `Queued Renamer` form)."""
    return status == QUEUED or status.startswith("Queued ")


def is_processing(status: str) -> bool:
    """`Processing` (and the older `Processing Renamer`; never `Processing complete`)."""
    return status == PROCESSING or (
        status.startswith("Processing ") and status != "Processing complete"
    )


def is_busy(status: str) -> bool:
    return is_queued(status) or is_processing(status)
