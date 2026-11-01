"""Find where the kirtan ends and the class begins (tools/kirtan-splitter/SPECS.md).

Works on a profile of the audio made locally (see `features.py`): for every second the
probability that someone is speaking, from a voice-activity model. Singing with instruments
scores near 0 and a class well above `SPEECH_AT`. No transcript is used; transcription belongs
to the class-discoverer.

Pure functions: no files, no database. The tool wraps them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MIN_KIRTAN_S = 90.0  # a shorter lead-in is announcements, not a kirtan
PLAUSIBLE_KIRTAN_S = (180.0, 2100.0)  # 3 to 35 minutes
CLASS_MIN_SPEECH_S = 20.0  # a speech run needs this long to start a class
SPEECH_AT = 0.10  # smoothed speech probability from which a second counts as speech
SMOOTH_S = 9  # seconds averaged, so a clap or a shout does not flip the label
MUSIC_SHARE_MIN = 0.7  # a kirtan is at least this much music (blips of speech are allowed)
SNAP_S = 3.0  # move the cut to a silence this close to it
LEAD_PAD_S = 0.3  # kept before the first sound
MAX_LEAD_S = 60.0  # a longer 'silence' at the start is a quiet recording, not a lead-in
TIMELINE_STEP_S = 10
# Where the music stops (found from the level and the high frequencies, see `music_end`)
DROP_DB = 20.0  # music is at most this far below the kirtan's own loud level
HIGH_KEEP = 0.4  # and keeps at least this share of the kirtan's high-frequency share
HIGH_MIN_REF = 0.03  # below this the kirtan has too little treble to tell music from voice
MUSIC_REF_PERCENTILE = 80  # the kirtan's 'own loud level'
BREAK_S = 4  # a stop must last this long (a short dip is a pause in the singing)
RESUME_S = 45  # music that comes back for this long means the stop was not the end
VOTE_S = 5  # each second is judged together with its neighbours (median of this many)


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


def smooth(profile: list[float | None], width: int = SMOOTH_S) -> list[float | None]:
    """Average over `width` seconds around each second (silent seconds are skipped)."""
    half = width // 2
    out: list[float | None] = []
    for i, value in enumerate(profile):
        if value is None:
            out.append(None)
            continue
        near = [v for v in profile[max(0, i - half) : i + half + 1] if v is not None]
        out.append(sum(near) / len(near))
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


def _median_vote(flags: list[bool], width: int = VOTE_S) -> list[bool]:
    half = width // 2
    out = []
    for i in range(len(flags)):
        window = flags[max(0, i - half) : i + half + 1]
        out.append(sum(window) * 2 > len(window))
    return out


def music_end(
    sound: Any, start: int, limit: int, min_music_s: float = MIN_KIRTAN_S
) -> tuple[int | None, dict[str, Any]]:
    """The second where the kirtan's music stops, between `start` and `limit` (the first speech).

    A second counts as music while it is loud (within DROP_DB of the kirtan's own loud level)
    and, when the kirtan has enough treble, still has its share of high frequencies. The kirtan
    ends at the first lasting stop (at least BREAK_S seconds) that comes after `min_music_s`
    of music and is not followed by music that returns for RESUME_S seconds.
    """
    n = min(limit, len(sound.level), len(sound.high))
    if n - start < min_music_s:
        return None, {}
    level = sound.level[start:n]
    high = sound.high[start:n]
    ref_level = sorted(level)[min(len(level) - 1, int(len(level) * MUSIC_REF_PERCENTILE / 100))]
    loud_high = [h for lv, h in zip(level, high, strict=True) if lv >= ref_level - 10]
    ref_high = sorted(loud_high)[len(loud_high) // 2] if loud_high else 0.0
    use_high = ref_high >= HIGH_MIN_REF
    flags = [
        lv >= ref_level - DROP_DB and (not use_high or h >= HIGH_KEEP * ref_high)
        for lv, h in zip(level, high, strict=True)
    ]
    music = _median_vote(flags)
    info = {
        "ref_level_db": round(ref_level, 1),
        "ref_high_share": round(ref_high, 3),
        "treble_used": use_high,
    }
    i = 0
    while i < len(music):
        if music[i]:
            i += 1
            continue
        j = i
        while j < len(music) and not music[j]:
            j += 1
        # the stop is [i, j); it must come after enough music and last long enough
        if j - i >= BREAK_S and sum(music[:i]) >= min_music_s:
            longest = run = 0
            for flag in music[j:]:
                run = run + 1 if flag else 0
                longest = max(longest, run)
            if longest < RESUME_S:
                return start + i, info
        i = j
    return None, info


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
    sound: Any = None,
) -> Decision:
    silences = silences or []
    start = lead_in(silences)
    smoothed = smooth(profile)
    labels = [None if v is None else ("speech" if v >= SPEECH_AT else "music") for v in smoothed]
    sounding = [v for v in smoothed if v is not None]
    features: dict[str, Any] = {"timeline": timeline(smoothed), "seconds": len(profile)}
    raw = [None if v is None else v for v in profile]
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
    # the smoothed run starts a few seconds early: take the first second that really speaks
    first_voice = next(
        (i for i in range(run_start, run_end) if raw[i] is not None and raw[i] >= SPEECH_AT),
        run_start,
    )
    # The voice model only sees speech; where the music stopped comes from the level and treble.
    stop, stop_info = (None, {})
    if sound is not None:
        stop, stop_info = music_end(sound, int(start), first_voice)
    last_music = stop if stop is not None else first_voice
    split_at, snapped = snap_to_silence(float(last_music), silences, float(first_voice))
    split_at = max(start + 1.0, min(split_at, float(first_voice)))
    kirtan_seconds = last_music - start
    before = [v for v in smoothed[int(start) : last_music] if v is not None]
    after = [v for v in smoothed[run_start:run_end] if v is not None]
    music_share = _mean([1.0 if v < SPEECH_AT else 0.0 for v in before])
    margin = _mean(after) - _mean(before)  # about 0.4 to 0.65 on real classes

    if stop is not None:
        score = 0.35
        reasons = [f"The music stops at {last_music}s; the first speech is at {first_voice}s."]
    else:
        score = 0.20
        reasons = [
            f"Speech from {first_voice}s; no clear end of the music was found before it, "
            "so the cut is at the speech."
        ]
    score += 0.25 * max(0.0, min(1.0, margin / 0.3))
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
        **stop_info,
        music_end=stop,
        first_speech=first_voice,
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
        class_start=float(first_voice),
        mantra=mantra or "Kirtan",
        snapped_to_silence=snapped,
        reasons=reasons,
        features=features,
    )
