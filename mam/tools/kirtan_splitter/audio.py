"""ffmpeg helpers for the splitter: silences and audio extraction from video."""

from __future__ import annotations

import re
from pathlib import Path

from mam.helpers.audio_optimizer.ffmpeg import binary, probe, run
from mam.helpers.file_organizer.organizer import assert_writable

SILENCE_DB = -40
SILENCE_MIN_S = 0.8


def find_silences(path: Path, duration: float | None = None) -> list[tuple[float, float]]:
    """Silent stretches (start, end) in seconds."""
    proc = run(
        [binary("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0"]
        + ["-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S}", "-f", "null", "-"],
        check=False,
    )
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        if m := re.search(r"silence_start: (-?[\d.]+)", line):
            start = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:  # silent until the end
        out.append((start, duration if duration is not None else probe(path).duration))
    return out


def extract_audio(video: Path, dst_stem: Path, *, dry_run: bool = True) -> Path:
    """Audio of a video as mp3 in its original quality (the bitrate of the audio stream).

    An mp3 stream is copied; any other codec is encoded once at its own bitrate.
    """
    info = probe(video)
    dst = dst_stem.with_suffix(".mp3")
    if dry_run:
        return dst
    assert_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "copy"] if info.codec == "mp3" else ["-c:a", "libmp3lame"]
    if info.codec != "mp3":
        bitrate = min(max(info.bitrate or 192_000, 128_000), 320_000)
        codec += ["-b:a", str(bitrate)]
    tmp = dst.with_name(f".{dst.stem}.part.mp3")
    try:
        run(
            [binary("ffmpeg"), "-hide_banner", "-nostats", "-y", "-i", str(video), "-vn"]
            + ["-map", "0:a:0", "-map_metadata", "0", *codec, str(tmp)]
        )
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst
