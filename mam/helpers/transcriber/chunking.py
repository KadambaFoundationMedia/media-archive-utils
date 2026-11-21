"""Pure helpers for robust transcription: chunk boundaries and repetition-loop cleanup."""

from __future__ import annotations

import re
import zlib

SAMPLE_RATE = 16000
LOOP_RATIO = 2.4  # Whisper's own compression-ratio threshold for a repetitive text
_WORD = re.compile(r"\S+")


def pick_boundaries(audio, chunk_s: float, search_s: float = 20.0) -> list[float]:
    """Chunk edges in seconds, each moved to the quietest spot within +-search_s of the
    nominal edge, so a chunk rarely starts in the middle of a word.

    `audio` is a 16 kHz mono float array (numpy or mlx); only its length and a numpy view
    of the search windows are used.
    """
    import numpy as np

    total = len(audio) / SAMPLE_RATE
    edges = [0.0]
    nominal = chunk_s
    while nominal < total - chunk_s / 2:  # no tiny last chunk
        lo = max(edges[-1] + chunk_s / 2, nominal - search_s)
        hi = min(total, nominal + search_s)
        window = np.asarray(audio[int(lo * SAMPLE_RATE) : int(hi * SAMPLE_RATE)], dtype="float32")
        frame = SAMPLE_RATE // 2  # 0.5 s frames
        n = len(window) // frame
        if n >= 2:
            energy = np.square(window[: n * frame].reshape(n, frame)).mean(axis=1)
            quiet = int(np.argmin(energy))
            edges.append(lo + quiet * 0.5 + 0.25)
        else:
            edges.append(nominal)
        nominal = edges[-1] + chunk_s
    edges.append(total)
    return edges


def looks_looped(text: str) -> bool:
    """True when a text is mostly one repeated phrase (a Whisper decoding loop)."""
    text = text.strip()
    if len(text) < 80:
        return False
    raw = text.encode("utf-8")
    return len(raw) / max(1, len(zlib.compress(raw))) > LOOP_RATIO


def collapse_loops(text: str, max_ngram: int = 8) -> str:
    """Keep one copy of a phrase that is repeated back to back ('smile, smile, smile...')."""
    words = _WORD.findall(text)
    if len(words) < 6:
        return text

    def key(w: str) -> str:
        return re.sub(r"\W+", "", w).lower()

    out: list[str] = []
    i = 0
    while i < len(words):
        collapsed = False
        for n in range(1, max_ngram + 1):
            gram = [key(w) for w in words[i : i + n]]
            if len(gram) < n or not any(gram):
                continue
            reps = 1
            while [key(w) for w in words[i + reps * n : i + (reps + 1) * n]] == gram:
                reps += 1
            if reps >= 3:  # three or more in a row is a loop, two can be speech
                out.extend(words[i : i + n])
                i += reps * n
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out)


# Lines Whisper invents over music and silence (seen over a kirtan: 'Shabbat Shalom').
INVENTED = {"thank you", "thanks for watching", "shabbat shalom", "shalom", "bye", "you", "please"}


# Credits and sign-offs Whisper writes over music or silence (never part of a class).
CREDIT_MARKERS = (
    "altyazı", "izlediğiniz", "subtitles by", "subtitled by", "amara.org", "thanks for watching",
    "thank you for watching", "shabbat shalom", "transcribed by", "satsang with mooji",
)  # fmt: skip


def is_invented_credit(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in CREDIT_MARKERS)


def transcript_quality(segments, notes: dict | None) -> dict:
    """How far to trust a transcript: the share of lines that are repeated loops or phrases
    Whisper invents, and whether a chunk had to be retried."""
    notes = notes or {}
    total = len(segments)
    invented = sum(1 for s in segments if s.text.strip().strip(".!?, ").lower() in INVENTED)
    loops = int(notes.get("loops_collapsed") or 0)
    dropped = int(notes.get("invented_dropped") or 0)
    seen = total + dropped
    bad = round((invented + loops + dropped) / seen, 3) if seen else 0.0
    retried = sum(1 for e in notes.get("events") or [] if "problem" in e)
    return {
        "bad_share": bad,
        "retried_chunks": retried,
        "unreliable": seen == 0 or bad > 0.25,
    }
