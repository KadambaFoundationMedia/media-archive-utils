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
        if not text or float(seg["end"]) - float(seg["start"]) < 0.02:
            continue  # empty, or a zero-length segment of a stuck decode
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


def _cpp_fallback_for(audio: Path, settings: Settings, language: str | None):
    """A function that gives one chunk to whisper.cpp, or None when it is not set up."""
    cli = settings.whisper_cli or shutil.which("whisper-cli")
    model = settings.whisper_cpp_model
    if not cli or model is None or not Path(model).is_file():
        return None
    from mam.helpers.audio_optimizer.ffmpeg import binary, run

    def chunk(start: float, end: float) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=settings.work_dir) as tmp:
            part = Path(tmp) / "chunk.wav"
            run([binary("ffmpeg"), "-hide_banner", "-nostats", "-y", "-ss", str(start),
                 "-t", str(end - start), "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000",
                 "-c:a", "pcm_s16le", str(part)])  # fmt: skip
            t = transcribe_whispercpp(part, settings, language)
        return {
            "language": t.language,
            "segments": [
                {"start": x.start, "end": x.end, "text": x.text, "avg_logprob": None}
                for x in t.segments
            ],
        }

    return chunk


def transcribe_mlx(audio: Path, settings: Settings, language: str | None) -> Transcript:
    try:
        import mlx_whisper  # noqa: F401
    except ImportError as exc:
        raise MamError("mlx-whisper is not installed. Run `uv sync` on the Mac.") from exc
    from importlib.metadata import PackageNotFoundError, version

    from mam.helpers.transcriber.chunking import (
        collapse_loops,
        is_invented_credit,
        looks_looped,
    )
    from mam.helpers.transcriber.mlx_runner import MlxRunner, run_chunks

    repo = settings.whisper_mlx_model
    results, events = run_chunks(
        lambda: MlxRunner(
            audio, repo, language, settings.transcribe_words, float(settings.transcribe_chunk_s)
        ),
        float(settings.transcribe_chunk_timeout_s),
        _cpp_fallback_for(audio, settings, language),
        None if language else settings.language_set(),
        "en",
    )
    segments: list[Segment] = []
    spans: dict[str, float] = {}
    loops = dropped = 0
    for chunk in results:
        offset = chunk["offset"]
        language_here = chunk.get("language")
        part = _mlx_segments(chunk, settings.transcribe_words)
        for seg in part:
            if is_invented_credit(seg.text):
                dropped += 1  # 'Altyazı M.K.', 'Shabbat Shalom': invented over music
                continue
            seg.start += offset
            seg.end += offset
            for w in seg.words or []:
                for key in ("s", "e"):
                    if w.get(key) is not None:
                        w[key] += offset
            if looks_looped(seg.text):
                loops += 1
                seg.text = collapse_loops(seg.text)
            segments.append(seg)
            if language_here:
                spans[language_here] = spans.get(language_here, 0.0) + (seg.end - seg.start)
    detected = language or (max(spans, key=spans.get) if spans else None)
    try:
        engine_version = version("mlx-whisper")
    except PackageNotFoundError:
        engine_version = None
    notes = {
        "chunks": len(results),
        "loops_collapsed": loops,
        "invented_dropped": dropped,
        "events": events,
    }
    return Transcript(
        segments=segments,
        language=detected,
        engine="mlx-whisper",
        engine_version=engine_version,
        model=repo,
        model_hash=None,
        backend="mlx",
        fallback=notes if (events or loops or dropped) else None,
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
        limit = float(settings.transcribe_chunk_timeout_s) * 4 + 600  # a whole recording

        def attempt(extra: list[str]) -> subprocess.CompletedProcess:
            try:
                return subprocess.run(
                    [cli, "-m", str(model), "-f", str(wav), "-oj", "-of", str(out),
                     "-l", language or "auto", "-t", str(threads), "-tp", "0.0", "-mc", "0",
                     *extra],
                    capture_output=True, text=True, check=False, timeout=limit,
                )  # fmt: skip
            except subprocess.TimeoutExpired as exc:
                raise MamError(f"whisper-cli made no result in {limit:.0f} s") from exc

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
