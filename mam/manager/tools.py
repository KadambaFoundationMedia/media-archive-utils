"""Tool registry: what the action menu can run, on which worker pool, and in what order."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mam.config import Settings
from mam.helpers.local_db import models as m
from mam.helpers.progress import report

# Full processing order (CONTRACTS.md). Tools not built yet are skipped by the pipeline.
PIPELINE_ORDER = [
    "renamer",
    "kirtan-splitter",
    "class-discoverer",
    "content-discoverer",
    "class-trimmer",
]


@dataclass
class JobContext:
    job_id: int
    session: Session
    settings: Settings
    dry_run: bool
    params: dict[str, Any]
    media_file: m.MediaFile | None = None


@dataclass
class ToolOutcome:
    """What a tool reports back. `state` drives the file status."""

    state: str  # done | review | skipped | corrupted | info
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    status: str | None = None  # a status of the tool's own, instead of "<Tool> Done"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    pool: str  # ml | cpu | io
    run: Callable[[JobContext], ToolOutcome]
    description: str
    per_file: bool = True
    changes_files: bool = True
    sets_status: bool = True  # read-only checks leave the processing status alone
    first_step: str | None = None  # a hidden tool that runs first, on its own pool
    hidden: bool = False  # a step of another tool: not in the action menu


def _scan(ctx: JobContext) -> ToolOutcome:
    from mam.manager.scanner import scan_folder

    report("Looking for media files in the folder")
    result = scan_folder(ctx.session, Path(ctx.params["path"]))
    return ToolOutcome(
        "info",
        f"{result.found} media files: {result.new} new, {result.known} known, "
        f"{result.duplicates} identical copies, {result.ignored} other files ignored",
        result.to_dict(),
    )


def _renamer(ctx: JobContext) -> ToolOutcome:
    from mam.tools.renamer.tool import run_renamer

    return run_renamer(ctx)


def _kirtan_splitter(ctx: JobContext) -> ToolOutcome:
    from mam.tools.kirtan_splitter.tool import run_kirtan_splitter

    return run_kirtan_splitter(ctx)


def _class_discoverer(ctx: JobContext) -> ToolOutcome:
    from mam.tools.class_discoverer.tool import run_class_discoverer

    return run_class_discoverer(ctx)


def _class_loudness(ctx: JobContext) -> ToolOutcome:
    from mam.tools.class_discoverer.tool import run_class_loudness

    return run_class_loudness(ctx)


def _content_discoverer(ctx: JobContext) -> ToolOutcome:
    from mam.tools.content_discoverer.tool import run_content_discoverer

    return run_content_discoverer(ctx)


TOOLS: dict[str, ToolSpec] = {
    "renamer": ToolSpec(
        "renamer",
        "Renamer",
        "io",
        _renamer,
        "Find the date, WHAT and WHERE, rename and copy to processed-files",
    ),
    "kirtan-splitter": ToolSpec(
        "kirtan-splitter",
        "Kirtan splitter",
        "cpu",
        _kirtan_splitter,
        "Find where the kirtan ends and split a combined recording into kirtan and class",
    ),
    "class-discoverer": ToolSpec(
        "class-discoverer",
        "Class discoverer",
        "ml",
        _class_discoverer,
        "Even out the volume, transcribe the class and find its verse on vedabase.io",
        first_step="class-loudness",
    ),
    # The loudness step of the Class discoverer: ffmpeg work, so it runs on the CPU pool, many
    # files at once, while the ML pool transcribes one file. It queues the transcription itself.
    "class-loudness": ToolSpec(
        "class-loudness",
        "Class discoverer",
        "cpu",
        _class_loudness,
        "Measure and even out the volume of a class before it is transcribed",
        hidden=True,
    ),
    "content-discoverer": ToolSpec(
        "content-discoverer",
        "Content discoverer",
        "io",
        _content_discoverer,
        "Write the themes, a title and the YouTube description from the transcript",
    ),
    "scan": ToolSpec(
        "scan",
        "Scan folder",
        "io",
        _scan,
        "Register media files in a folder",
        per_file=False,
        changes_files=False,
        sets_status=False,
    ),
}


def menu_tools() -> list[ToolSpec]:
    return [t for t in TOOLS.values() if t.per_file and not t.hidden]


def next_in_pipeline(tool: str) -> str | None:
    if tool not in PIPELINE_ORDER:
        return None
    for name in PIPELINE_ORDER[PIPELINE_ORDER.index(tool) + 1 :]:
        if name in TOOLS:
            return name
    return None


def pipeline_start() -> str:
    return next(name for name in PIPELINE_ORDER if name in TOOLS)
