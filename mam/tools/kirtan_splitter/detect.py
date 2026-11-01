"""Find where the kirtan ends and the class begins (tools/kirtan-splitter/SPECS.md).

Works on a profile of the audio made locally (see `features.py`): for every second a score from
0 (music: singing with instruments) to 1 (speech), or `None` where the recording is silent. No
transcript is used; transcription belongs to the class-discoverer.

Pure functions: no files, no database. The tool wraps them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MIN_KIRTAN_S = 90.0  # a shorter lead-in is announcements, not a kirtan
PLAUSIBLE_KIRTAN_S = (180.0, 2100.0)  # 3 to 35 minutes
CLASS_MIN_SPEECH_S = 20.0  # a speech run needs this long to start a class
SPEECH_AT = 0.5  # profile score from which a second counts as speech
MUSIC_SHARE_MIN = 0.7  # a kirtan is at least this much music (blips of speech are allowed)
SNAP_S = 3.0  # move the cut to a silence this close to it
LEAD_PAD_S = 0.3  # kept before the first sound
MAX_LEAD_S = 60.0  # a longer 'silence' at the start is a quiet recording, not a lead-in
TIMELINE_STEP_S = 10


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


def smooth(profile: list[float | None], width: int = 5) -> list[float | None]:
    """Median over `width` seconds, so a clap or a shout does not flip the label."""
    half = width // 2
    out: list[float | None] = []
    for i, value in enumerate(profile):
        if value is None:
            out.append(None)
            continue
        near = sorted(v for v in profile[max(0, i - half) : i + half + 1] if v is not None)
        out.append(near[len(near) // 2])
    return out


def timeline(profile: list[float | None]) -> str:
    """One character per 10 s: m music, s speech, . silence. For reviews and tuning."""
    chars = []
    for i in range(0, len(profile), TIMELINE_STEP_S):
        chunk = [v for v in profile[i : i + TIMELINE_STEP_S] if v is not None]
        if not chunk:
            chars.append(".")
        else:
            chars.append("s" if sum(chunk) / len(chunk) >= SPEECH_AT else "m")
    return "".join(chars)


def snap_to_silence(t: float, silences: list[tuple[float, float]], limit: float):
    """Move `t` into a silence within SNAP_S (cut just inside its start)."""
    best = None
    for start, end in silences:
        if start > limit:
            continue
        if start - SNAP_S <= t <= end + SNAP_S:
            point = min(max(t, start), end)
            cut = min(point, start + 0.5)
            if best is None or abs(cut - t) < abs(best - t):
                best = cut
    return (best, True) if best is not None else (t, False)


def lead_in(silences: list[tuple[float, float]]) -> float:
    """Where the first sound starts: skip silence at the very beginning."""
    if silences and silences[0][0] <= 0.5 and silences[0][1] <= MAX_LEAD_S:
        return max(0.0, silences[0][1] - LEAD_PAD_S)
    return 0.0


def _runs(labels: list[str | None], kind: str) -> list[tuple[int, int]]:
    """(start, end) second ranges of `kind`; silence does not break a run."""
    runs: list[tuple[int, int]] = []
    start = last = None
    for i, label in enumerate(labels):
        if label == kind:
            if start is None:
                start = i
            last = i
        elif label is not None and start is not None:  # the other kind ends the run
            runs.append((start, last + 1))
            start = last = None
    if start is not None:
        runs.append((start, last + 1))
    return runs


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def detect(
    profile: list[float | None],
    duration: float,
    silences: list[tuple[float, float]] | None = None,
    mantra: str | None = None,
) -> Decision:
    silences = silences or []
    start = lead_in(silences)
    smoothed = smooth(profile)
    labels = [None if v is None else ("speech" if v >= SPEECH_AT else "music") for v in smoothed]
    sounding = [v for v in smoothed if v is not None]
    features: dict[str, Any] = {"timeline": timeline(smoothed), "seconds": len(profile)}
    if len(sounding) < 30:
        return Decision(
            "unclear",
            0.2,
            kirtan_start=start,
            reasons=["There is hardly any sound to analyse."],
            features=features,
        )

    class_run = None
    first_speech = None  # the first speech run long enough to be a class
    for run_start, run_end in _runs(labels, "speech"):
        if run_end - run_start < CLASS_MIN_SPEECH_S:
            continue
        music_before = sum(1 for lab in labels[int(start) : run_start] if lab == "music")
        if first_speech is None:
            first_speech = (run_start, music_before)
        if music_before >= MIN_KIRTAN_S:
            class_run = (run_start, run_end)
            break

    if class_run is None and first_speech is not None:
        run_start, music_before = first_speech
        return Decision(
            "class_only",
            0.9 if music_before == 0 and run_start < 60 else 0.7,
            kirtan_start=start,
            class_start=float(run_start),
            mantra=mantra,
            reasons=[
                f"The recording starts with speech; there is no kirtan of at least "
                f"{int(MIN_KIRTAN_S)} seconds before it."
            ],
            features={**features, "first_speech_at": run_start},
        )

    if class_run is None:
        music_share = _mean([1.0 if lab == "music" else 0.0 for lab in labels if lab])
        features["music_share"] = round(music_share, 3)
        if music_share >= MUSIC_SHARE_MIN:
            return Decision(
                "kirtan_only",
                round(min(0.95, 0.5 + 0.45 * music_share), 3),
                kirtan_start=start,
                mantra=mantra,
                reasons=["No sustained speech: the music continues through the recording."],
                features=features,
            )
        return Decision(
            "unclear",
            0.3,
            kirtan_start=start,
            reasons=["Neither a clear kirtan nor a clear class: speech and music alternate."],
            features=features,
        )

    run_start, run_end = class_run
    last_music = max(i for i in range(int(start), run_start) if labels[i] == "music") + 1
    split_at, snapped = snap_to_silence(float(last_music), silences, float(run_start))
    split_at = max(start + 1.0, min(split_at, float(run_start)))
    kirtan_seconds = last_music - start
    before = [v for v in smoothed[int(start) : last_music] if v is not None]
    after = [v for v in smoothed[run_start:run_end] if v is not None]
    music_share = _mean([1.0 if v < SPEECH_AT else 0.0 for v in before])
    margin = _mean(after) - _mean(before)

    score = 0.35
    reasons = [f"Music until {last_music}s, speech from {run_start}s."]
    score += 0.25 * max(0.0, min(1.0, margin / 0.6))
    if snapped:
        score += 0.15
        reasons.append("The cut is inside a silence.")
    score += 0.10 * max(0.0, min(1.0, (music_share - 0.5) / 0.4))
    lo, hi = PLAUSIBLE_KIRTAN_S
    if lo <= kirtan_seconds <= hi:
        score += 0.10
    else:
        score -= 0.2
        reasons.append(f"The kirtan is {kirtan_seconds / 60:.1f} minutes, which is unusual.")
    if mantra:
        score += 0.05
        reasons.append(f"The mantra is {mantra} (from the class).")
    else:
        reasons.append("The class does not tell the mantra; the kirtan is named Kirtan.")
    features.update(
        music_share=round(music_share, 3),
        contrast=round(margin, 3),
        kirtan_seconds=round(kirtan_seconds, 1),
        class_run_seconds=run_end - run_start,
    )
    return Decision(
        "split",
        round(max(0.0, min(1.0, score)), 3),
        kirtan_start=start,
        split_at=round(split_at, 3),
        class_start=float(run_start),
        mantra=mantra or "Kirtan",
        snapped_to_silence=snapped,
        reasons=reasons,
        features=features,
    )
