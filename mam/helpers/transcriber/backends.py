"""Transcription backends. Both return a `Transcript` with the same segment contract."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mam.config import Settings
from mam.errors import MamError
from mam.helpers.transcriber.models import Segment, Transcript

log = logging.getLogger(__name__)


def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def mlx_available() -> bool:
    if not apple_silicon():
        return False
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def pick_backend(settings: Settings) -> str:
    """'mlx' on Apple Silicon when mlx-whisper is installed, else 'whispercpp'."""
    choice = settings.transcriber_backend
    if choice in ("mlx", "whispercpp"):
        return choice
    return "mlx" if mlx_available() else "whispercpp"


# ------------------------------------------------------------------------- mlx
def _mlx_segments(result: dict[str, Any], with_words: bool) -> list[Segment]:
    language = result.get("language")
    segments = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        logprob = seg.get("avg_logprob")
        words = None
        if with_words and seg.get("words"):
            words = [
                {"w": w.get("word", "").strip(), "s": w.get("start"), "e": w.get("end")}
                for w in seg["words"]
            ]
        segments.append(
            Segment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                text=text,
                language=language,
                confidence=None if logprob is None else float(logprob),
                words=words,
            )
        )
    return segments


def transcribe_mlx(audio: Path, settings: Settings, language: str | None) -> Transcript:
    try:
        import mlx_whisper
    except ImportError as exc:
        raise MamError("mlx-whisper is not installed. Run `uv sync` on the Mac.") from exc
    from importlib.metadata import PackageNotFoundError, version

    repo = settings.whisper_mlx_model
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        language=language,
        temperature=0.0,
        condition_on_previous_text=False,  # no carried context: no runaway repetition
        word_timestamps=settings.transcribe_words,
        verbose=None,
    )
    try:
        engine_version = version("mlx-whisper")
    except PackageNotFoundError:
        engine_version = None
    segments = _mlx_segments(result, settings.transcribe_words)
    return Transcript(
        segments=segments,
        language=result.get("language") or language,
        engine="mlx-whisper",
        engine_version=engine_version,
        model=repo,
        model_hash=None,
        backend="mlx",
    )


# ------------------------------------------------------------------ whisper.cpp
def whisper_cli_path(settings: Settings) -> str:
    found = settings.whisper_cli or shutil.which("whisper-cli")
    if not found:
        raise MamError("whisper-cli was not found. Install whisper.cpp (winget or Homebrew).")
    return found


def cpp_segments(data: dict[str, Any]) -> tuple[list[Segment], str | None]:
    """Normalize whisper-cli's `-oj` JSON (millisecond offsets) into segments."""
    language = (data.get("result") or {}).get("language")
    segments = []
    for item in data.get("transcription", []):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        off = item.get("offsets") or {}
        segments.append(
            Segment(
                start=off.get("from", 0) / 1000,
                end=off.get("to", 0) / 1000,
                text=text,
                language=language,
            )
        )
    return segments, language


def transcribe_whispercpp(audio: Path, settings: Settings, language: str | None) -> Transcript:
    cli = whisper_cli_path(settings)
    model = settings.whisper_cpp_model
    if model is None or not Path(model).is_file():
        raise MamError(
            "The whisper.cpp model (ggml-large-v3-turbo.bin) was not found. "
            "Set WHISPER_CPP_MODEL in .env."
        )
    from mam.helpers.audio_optimizer.ffmpeg import binary, run

    with tempfile.TemporaryDirectory(dir=settings.work_dir) as tmp:
        wav = Path(tmp) / "audio.wav"
        run([binary("ffmpeg"), "-hide_banner", "-nostats", "-y", "-i", str(audio), "-vn",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])  # fmt: skip
        out = Path(tmp) / "out"
        threads = max(2, min(8, (os.cpu_count() or 4) - 2))

        def attempt(extra: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                [cli, "-m", str(model), "-f", str(wav), "-oj", "-of", str(out),
                 "-l", language or "auto", "-t", str(threads), "-tp", "0.0", "-mc", "0", *extra],
                capture_output=True, text=True, check=False,
            )  # fmt: skip

        fallback = None
        proc = attempt([])
        if proc.returncode != 0 and settings.whisper_device == "auto":
            fallback = {"from": "metal", "to": "cpu", "reason": proc.stderr[-300:]}
            proc = attempt(["-ng"])  # no GPU: local CPU
        if proc.returncode != 0:
            raise MamError(f"whisper-cli failed: {proc.stderr[-300:]}")
        data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    segments, detected = cpp_segments(data)
    ver = subprocess.run([cli, "--help"], capture_output=True, text=True, check=False)
    return Transcript(
        segments=segments,
        language=detected or language,
        engine="whisper.cpp",
        engine_version=None if ver.returncode else "whisper-cli",
        model=Path(model).name,
        model_hash=None,
        backend="cpu" if fallback or sys.platform == "win32" else "metal",
        fallback=fallback,
    )


def run_backend(audio: Path, settings: Settings, language: str | None) -> Transcript:
    if pick_backend(settings) == "mlx":
        return transcribe_mlx(audio, settings, language)
    return transcribe_whispercpp(audio, settings, language)
