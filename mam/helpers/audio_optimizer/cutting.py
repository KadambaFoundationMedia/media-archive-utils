"""Lossless cuts: keep time ranges by stream copy in the original codec (no re-encode).

Precision is limited to the codec's frame size (about 26 ms for MP3), which is well within the
review tolerance. Used by the kirtan-splitter and class-trimmer.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from mam.helpers.audio_optimizer.ffmpeg import binary, run
from mam.helpers.file_organizer.organizer import assert_writable

Range = tuple[float, float | None]  # seconds; None end = until the end of the file


def stream_cut(src: Path, dst: Path, keep: Sequence[Range], *, dry_run: bool = True) -> list[str]:
    """Write `dst` containing only the `keep` ranges of `src`. Returns the ffmpeg commands."""
    if not keep:
        raise ValueError("No ranges to keep")
    ordered = sorted(keep, key=lambda r: r[0])
    commands: list[list[str]] = []
    # Final output is written next to dst first, then renamed (same filesystem).
    tmp = dst.with_name(f".{dst.stem}.part{dst.suffix}")
    with tempfile.TemporaryDirectory(prefix="mam-cut-") as work:
        parts: list[Path] = []
        for i, (start, end) in enumerate(ordered):
            single = len(ordered) == 1
            part = tmp if single else Path(work) / f"part{i:03d}{src.suffix.lower()}"
            cmd = [binary("ffmpeg"), "-hide_banner", "-nostats", "-y", "-ss", f"{start:.3f}"]
            if end is not None:
                cmd += ["-to", f"{end:.3f}"]
            cmd += ["-i", str(src), "-map", "0:a:0", "-map_metadata", "0", "-c", "copy", str(part)]
            commands.append(cmd)
            parts.append(part)

        if len(parts) > 1:
            listing = Path(work) / "parts.txt"
            listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
            commands.append(
                [
                    binary("ffmpeg"),
                    "-hide_banner",
                    "-nostats",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    str(tmp),
                ]
            )
        if dry_run:
            return [" ".join(c) for c in commands]

        assert_writable(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            for cmd in commands:
                run(cmd)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
    return [" ".join(c) for c in commands]
