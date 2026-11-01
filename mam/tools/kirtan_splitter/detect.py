"""Find where the kirtan ends and the class begins (tools/kirtan-splitter/SPECS.md).

Evidence (SPEC.md sections 4 and 10): the singing/speech classification of the audio
(`classify.py`, local, no transcription) and the silence at the boundary. A kirtan is singing
from the start; the class is the first sustained speech after it.

Pure functions: no files, no database. The tool wraps them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mam.tools.kirtan_splitter.classify import HOP_S, WINDOW_S, Window

MIN_KIRTAN_S = 90.0  # a shorter lead-in is announcements, not a kirtan
PLAUSIBLE_KIRTAN_S = (180.0, 2100.0)  # 3 to 35 minutes
SNAP_S = 3.0  # move the cut to a silence this close to it
CLASS_RUN_S = 30.0  # speech must hold for this long to be the class
CLASS_RUN_SHARE = 0.75  # ...with this share of the windows spoken
KIRTAN_MIN_SING_SHARE = 0.5  # the lead-in must be at least this sung to be a kirtan
LEAD_PAD_S = 0.3  # kept before the first sound
MAX_LEAD_S = 60.0  # a longer 'silence' at the start is a quiet recording, not a lead-in


@dataclass
class Decision:
    mode: str  # split | kirtan_only | class_only | unclear
    score: float
    kirtan_start: float = 0.0
    split_at: float | None = None
    class_start: float | None = None
    mantra: str | None = None
    snapped_to_silence: bool = False
    reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snap_to_silence(
    t: float, silences: list[tuple[float, float]], limit: float
) -> tuple[float, bool]:
    """Silence start nearest to `t` (within SNAP_S); the cut is made inside the silence."""
    best = None
    for start, end in silences:
        if start > limit:
            continue
        if start - SNAP_S <= t <= end + SNAP_S:
            point = min(max(t, start), end) if start <= t <= end else (start if t < start else end)
            cut = min(point, start + 0.5)
            if best is None or abs(cut - t) < abs(best - t):
                best = cut
    return (best, True) if best is not None else (t, False)


def lead_in(silences: list[tuple[float, float]]) -> float:
    """Where the first sound starts: skip silence at the very beginning."""
    if silences and silences[0][0] <= 0.5 and silences[0][1] <= MAX_LEAD_S:
        return max(0.0, silences[0][1] - LEAD_PAD_S)
    return 0.0


def _share(windows: list[Window], kind: str) -> float:
    return sum(w.kind == kind for w in windows) / len(windows) if windows else 0.0


def _run_starts(windows: list[Window]) -> list[int]:
    """Indexes of the windows that start a sustained run of speech."""
    need = max(1, round(CLASS_RUN_S / HOP_S))
    return [
        i
        for i, w in enumerate(windows)
        if w.kind == "speech" and _share(windows[i : i + need], "speech") >= CLASS_RUN_SHARE
    ]


def _kirtan_before(windows: list[Window], run: int, start: float) -> bool:
    """Is there a kirtan (enough steady singing) before the speech run at `run`?"""
    before = [w for w in windows[:run] if w.start >= start]
    sung = sum(w.kind == "singing" for w in before) * HOP_S
    return sung >= MIN_KIRTAN_S and _share(before, "singing") >= 0.5


def _class_run_start(windows: list[Window], start: float) -> int | None:
    """The first speech run that follows a kirtan; else the first run (a class without kirtan)."""
    runs = _run_starts(windows)
    return next((i for i in runs if _kirtan_before(windows, i, start)), runs[0] if runs else None)


def detect(
    windows: list[Window],
    duration: float,
    silences: list[tuple[float, float]] | None = None,
    mantra: str | None = None,
) -> Decision:
    silences = silences or []
    start = lead_in(silences)
    mantra = mantra or "Kirtan"
    if not windows:
        return Decision(
            "unclear", 0.2, kirtan_start=start, reasons=["The audio is too short or silent."]
        )
    run = _class_run_start(windows, start)
    features: dict[str, Any] = {
        "windows": len(windows),
        "singing_share": round(_share(windows, "singing"), 3),
        "speech_share": round(_share(windows, "speech"), 3),
    }

    if run is None:
        sung = _share(windows, "singing")
        if sung < KIRTAN_MIN_SING_SHARE:
            return Decision(
                "unclear",
                0.3,
                kirtan_start=start,
                mantra=mantra,
                reasons=["Neither steady singing nor a sustained class was found."],
                features=features,
            )
        return Decision(
            "kirtan_only",
            round(min(0.95, 0.5 + 0.45 * sung), 3),
            kirtan_start=start,
            mantra=mantra,
            reasons=["No sustained speech: the singing continues through the recording."],
            features=features,
        )

    class_start = windows[run].start
    before = [w for w in windows[:run] if w.start >= start]
    sung_idx = [i for i, w in enumerate(windows[:run]) if w.kind == "singing" and w.start >= start]
    features.update(class_run_start=round(class_start, 2), sung_windows_before=len(sung_idx))
    last_sung_end = windows[sung_idx[-1]].start + WINDOW_S if sung_idx else start
    kirtan_seconds = last_sung_end - start

    if not sung_idx or kirtan_seconds < MIN_KIRTAN_S or _share(before, "singing") < 0.3:
        return Decision(
            "class_only",
            0.9 if class_start < 60 else 0.7,
            kirtan_start=start,
            class_start=class_start,
            reasons=[
                "The recording starts with speech; there is no kirtan of at least "
                f"{int(MIN_KIRTAN_S)} seconds before it."
            ],
            features=features,
        )

    last_sung_end = min(last_sung_end, class_start)
    split_at, snapped = snap_to_silence(last_sung_end, silences, class_start)
    split_at = max(start + 1.0, min(split_at, class_start))
    sing_share = _share(before, "singing")
    after = windows[run : run + round(60 / HOP_S)]
    speech_after = _share(after, "speech")
    gap = class_start - last_sung_end

    score = 0.35
    reasons = [f"Singing until {last_sung_end:.1f}s, speech from {class_start:.1f}s."]
    if snapped:
        score += 0.20
        reasons.append("The cut is inside a silence.")
    elif gap <= 2 * HOP_S:
        score += 0.10
        reasons.append("The change from singing to speech is sharp.")
    score += 0.15 * sing_share
    score += 0.15 * speech_after
    lo, hi = PLAUSIBLE_KIRTAN_S
    if lo <= kirtan_seconds <= hi:
        score += 0.10
    else:
        score -= 0.2
        reasons.append(f"The kirtan is {kirtan_seconds / 60:.1f} minutes, which is unusual.")
    if gap > 60:
        score -= 0.15
        reasons.append(f"{gap:.0f} s between the last singing and the class: talk or silence.")
    features.update(
        singing_share_before=round(sing_share, 3),
        speech_share_after=round(speech_after, 3),
        kirtan_seconds=round(kirtan_seconds, 1),
        gap_s=round(gap, 1),
    )
    return Decision(
        "split",
        round(max(0.0, min(1.0, score)), 3),
        kirtan_start=start,
        split_at=round(split_at, 3),
        class_start=round(class_start, 3),
        mantra=mantra,
        snapped_to_silence=snapped,
        reasons=reasons,
        features=features,
    )
