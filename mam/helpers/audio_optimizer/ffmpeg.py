"""Thin wrappers around ffprobe/ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mam.errors import CorruptedMedia, MamError


def binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MamError(
            f"{name} not found on PATH (macOS: brew install ffmpeg; Windows: winget install ffmpeg)"
        )
    return path


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise MamError(
            f"{Path(args[0]).name} failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}"
        )
    return proc


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    format_name: str
    codec: str
    bitrate: int | None  # bits per second
    sample_rate: int
    channels: int
    duration: float
    has_video: bool


def probe(path: Path) -> AudioInfo:
    """Codec, bitrate, sample rate, channels and duration of the first audio stream."""
    proc = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise CorruptedMedia(f"ffprobe cannot read {path.name}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise CorruptedMedia(f"{path.name} has no audio stream")
    fmt = data.get("format", {})
    bitrate = audio.get("bit_rate") or fmt.get("bit_rate")
    duration = audio.get("duration") or fmt.get("duration") or 0
    return AudioInfo(
        path=path,
        format_name=fmt.get("format_name", ""),
        codec=audio.get("codec_name", ""),
        bitrate=int(bitrate) if bitrate else None,
        sample_rate=int(audio.get("sample_rate", 0)),
        channels=int(audio.get("channels", 0)),
        duration=float(duration),
        has_video=any(
            s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
            for s in streams
        ),
    )


def verify_decode(path: Path) -> list[str]:
    """Strict full decode; returns the decoder errors (empty list means the file is clean)."""
    proc = run(
        [
            binary("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    errors = [line for line in proc.stderr.splitlines() if line.strip()]
    if proc.returncode != 0 and not errors:
        errors = [f"ffmpeg exited with {proc.returncode}"]
    return errors
