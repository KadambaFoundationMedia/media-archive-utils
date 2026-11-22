"""Second pass on the chanted verse (Settings: "Improve the chanted verse"; decided 2026-11-22).

Whisper writes chanted Sanskrit by ear. Once the class discoverer has confirmed the verse by
other evidence (the announcement and the English translation, or the loose Sanskrit match), the
stretch where the verse is chanted is transcribed again with the verse's transliteration from
vedabase.io as Whisper's prompt, which steers it to the right words. The prompt is never used
before the verse is confirmed, because a prompt can pull the text towards the wrong verse.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import Any

from mam.config import Settings
from mam.helpers.transcriber.chunking import collapse_loops, is_invented_credit, looks_looped
from mam.helpers.transcriber.models import Segment, Transcript

log = logging.getLogger(__name__)

WINDOW_SEGMENTS = 8  # the stretch matched by the loose Sanskrit comparison (match.py)
PADDING_S = 2.0


def verse_prompt(transliteration: str) -> str:
    """The verse as plain letters in one line (diacritics are not written by Whisper)."""
    text = unicodedata.normalize("NFKD", transliteration)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.replace("’", "'").split())


def span_of(transcript: Transcript, anchor: float) -> tuple[float, float] | None:
    """Start and end (seconds) of the stretch that begins at the segment starting at `anchor`."""
    segs = transcript.segments
    for i, seg in enumerate(segs):
        if abs(seg.start - anchor) < 0.01:
            last = segs[min(len(segs), i + WINDOW_SEGMENTS) - 1]
            return max(0.0, seg.start - PADDING_S), last.end + PADDING_S
    return None


def improve(
    audio: Path, settings: Settings, transcript: Transcript, transliteration: str, anchor: float
) -> dict[str, Any] | None:
    """Transcribe the chanting again with the verse as prompt and put it in `transcript`.

    Returns what was done, or None when nothing was changed (not the mlx backend, no stretch
    found, or Whisper answered with nothing usable or with a loop).
    """
    from mam.helpers.transcriber.backends import _mlx_segments, pick_backend

    if pick_backend(settings) != "mlx":
        return None
    span = span_of(transcript, anchor)
    if span is None:
        return None
    start, end = span
    from mam.helpers.transcriber.mlx_runner import MlxRunner

    runner = MlxRunner(
        audio,
        settings.whisper_mlx_model,
        None,
        False,
        float(settings.transcribe_chunk_s),
        verse_prompt(transliteration),
        False,
    )
    timeout = float(settings.transcribe_chunk_timeout_s)
    try:
        runner.start(timeout=max(120.0, timeout))
        language = transcript.language or "en"
        result = runner.run_chunk(
            2000, start, end, {"temperature": 0.0, "language": language}, timeout
        )
    except Exception:
        log.warning("The second pass on the chanted verse failed", exc_info=True)
        return None
    finally:
        runner.stop()
    fresh = []
    for seg in _mlx_segments(result, False):
        if is_invented_credit(seg.text):
            continue
        if looks_looped(seg.text):
            seg.text = collapse_loops(seg.text)
        fresh.append(Segment(seg.start + start, seg.end + start, seg.text, language))
    if not fresh:
        return None
    kept = [s for s in transcript.segments if s.end <= start or s.start >= end]
    before = len(transcript.segments) - len(kept)  # segments of the first pass in this stretch
    transcript.segments = sorted(kept + fresh, key=lambda s: (s.start, s.end))
    return {"start": start, "end": end, "segments": len(fresh), "segments_before": before}
