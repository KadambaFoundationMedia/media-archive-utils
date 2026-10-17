"""Environment checks for `mam doctor`."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass

from mam.config import Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def run_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("Python 3.12", sys.version_info[:2] == (3, 12), platform.python_version()))
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks.append(Check(tool, path is not None, path or "not found on PATH"))

    is_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    if is_apple_silicon:
        try:
            import mlx_whisper  # noqa: F401

            checks.append(Check("mlx-whisper", True, "installed"))
        except ImportError:
            checks.append(
                Check(
                    "mlx-whisper", False, "not installed yet (needed from class-discoverer)", False
                )
            )
    whisper_cli = shutil.which("whisper-cli")
    checks.append(
        Check(
            "whisper-cli",
            whisper_cli is not None,
            whisper_cli or "not found (needed on Windows from class-discoverer)",
            required=not is_apple_silicon,
        )
    )

    try:
        settings.ensure_dirs()
        probe_file = settings.local_db_path.parent / ".write-test"
        probe_file.write_text("ok")
        probe_file.unlink()
        checks.append(Check("Local DB folder writable", True, str(settings.local_db_path)))
    except OSError as exc:
        checks.append(Check("Local DB folder writable", False, str(exc)))

    processed = settings.media_processed_files_path.resolve()
    inside_ro = [r for r in settings.read_only_roots if r == processed or r in processed.parents]
    checks.append(
        Check(
            "Processed path outside originals",
            not inside_ro,
            str(processed) if not inside_ro else f"{processed} is inside {inside_ro[0]}",
        )
    )
    checks.append(
        Check(
            "Sample files folder",
            settings.media_sample_files_path.is_dir(),
            str(settings.media_sample_files_path),
        )
    )

    missing = [
        name
        for name in (
            "baserow_api_token",
            "baserow_media_table_id",
            "baserow_travel_schedule_table_id",
            "baserow_category_terms_table_id",
        )
        if not getattr(settings, name)
    ]
    checks.append(
        Check(".env Baserow keys", not missing, "all set" if not missing else f"missing: {missing}")
    )
    if not missing:
        checks.extend(_baserow_checks(settings))
    checks.append(
        Check(
            "Baserow writes",
            True,
            "APPLY (live writes enabled)" if settings.baserow_apply else "dry-run (default)",
            required=False,
        )
    )
    return checks


def _baserow_checks(settings: Settings) -> list[Check]:
    from mam.helpers.baserow import BaserowClient

    out: list[Check] = []
    try:
        with BaserowClient(settings) as client:
            for label, table in (
                ("media", client.media_table),
                ("travel_schedule", client.travel_table),
                ("category_terms_folder", client.category_table),
            ):
                fields = client.fields(table)
                out.append(Check(f"Baserow {label} table", True, f"{len(fields)} columns"))
    except Exception as exc:  # report, don't crash the doctor
        out.append(Check("Baserow reachable", False, str(exc)[:200]))
    return out
