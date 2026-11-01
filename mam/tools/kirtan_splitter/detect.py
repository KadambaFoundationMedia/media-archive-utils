"""Find where the kirtan ends and the class begins (tools/kirtan-splitter/SPECS.md).

Evidence (SPEC.md sections 4 and 10): the shared transcript, common phrases, and the silence at
the boundary. Segments are labelled *speech* or *singing* from their text: singing is sparse,
repetitive and made of mantra words; speech has many different words at a normal pace. No audio
classifier is used yet; the labeled examples that review outcomes produce decide later whether
one is needed.

Pure functions: no files, no database. The tool wraps them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mam.helpers.transcriber import Segment
from mam.tools.kirtan_splitter import phrases

MIN_KIRTAN_S = 90.0  # a shorter lead-in is announcements, not a kirtan
PLAUSIBLE_KIRTAN_S = (180.0, 2100.0)  # 3 to 35 minutes
SNAP_S = 3.0  # move the cut to a silence this close to it
SPEECH_MIN_WORDS = 6
SPEECH_MIN_UNIQUE = 0.55
SPEECH_MIN_RATE = 1.0  # words per second
SPEECH_GAP_S = 8.0  # speech segments closer than this belong to one run
CLASS_MIN_SPEECH_S = 20.0  # a speech run needs this much speech, or a marker, to start a class
LEAD_PAD_S = 0.3  # kept before the first sound
MAX_LEAD_S = 60.0  # a longer 'silence' at the start is a quiet recording, not a lead-in


@dataclass
class Labelled:
    seg: Segment
    kind: str  # speech | singing
    marker: str | None = None


@dataclass
class Decision:
    mode: str  # split | kirtan_only | class_only | unclear
    score: float
    kirtan_start: float = 0.0
    split_at: float | None = None
    class_start: float | None = None
    mantra: str | None = None
    marker: str | None = None
    snapped_to_silence: bool = False
    reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def label(seg: Segment) -> Labelled:
    ws = phrases.words(seg.text)
    marker = phrases.end_marker(seg.text)
    if marker:
        return Labelled(seg, "speech", marker)
    if len(ws) < SPEECH_MIN_WORDS:
        return Labelled(seg, "singing")
    unique = len(set(ws)) / len(ws)
    rate = len(ws) / max(seg.end - seg.start, 0.1)
    if (
        unique >= SPEECH_MIN_UNIQUE
        and rate >= SPEECH_MIN_RATE
        and phrases.mantra_share(seg.text) < 0.5
    ):
        return Labelled(seg, "speech")
    return Labelled(seg, "singing")


def speech_runs(items: list[Labelled]) -> list[list[Labelled]]:
    runs: list[list[Labelled]] = []
    for item in items:
        if item.kind != "speech":
            continue
        if runs and item.seg.start - runs[-1][-1].seg.end <= SPEECH_GAP_S:
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _run_speech_s(run: list[Labelled]) -> float:
    return sum(i.seg.end - i.seg.start for i in run)


def snap_to_silence(
    t: float, silences: list[tuple[float, float]], limit: float
) -> tuple[float, bool]:
    """Silence start nearest to `t` (within SNAP_S); the cut is made inside the silence."""
    best = None
    for start, end in silences:
        if start > limit:
            continue
        if start - SNAP_S <= t <= end + SNAP_S:
            # inside or next to a silence: cut a little into it
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


def detect(
    segments: list[Segment],
    duration: float,
    silences: list[tuple[float, float]] | None = None,
) -> Decision:
    silences = silences or []
    start = lead_in(silences)
    items = [label(s) for s in segments if s.text.strip()]
    if not items:
        return Decision(
            "unclear",
            0.2,
            kirtan_start=start,
            reasons=["The transcript is empty (no speech or singing recognised)."],
        )
    runs = speech_runs(items)

    class_run = None
    for run in runs:
        if any(i.marker for i in run) or _run_speech_s(run) >= CLASS_MIN_SPEECH_S:
            class_run = run
            break
    features: dict[str, Any] = {"segments": len(items), "speech_runs": len(runs)}

    if class_run is None:
        sung = sum(i.seg.end - i.seg.start for i in items if i.kind == "singing")
        share = sung / max(sum(i.seg.end - i.seg.start for i in items), 1.0)
        score = round(min(0.95, 0.5 + 0.45 * share) if not runs else 0.55, 3)
        return Decision(
            "kirtan_only",
            score,
            kirtan_start=start,
            mantra=phrases.mantra_type(" ".join(i.seg.text for i in items)),
            reasons=["No sustained speech: the singing continues through the recording."],
            features={**features, "singing_share": round(share, 3)},
        )

    first = class_run[0]
    marker_item = next((i for i in class_run if i.marker), None)
    class_start = (marker_item or first).seg.start
    before = [i for i in items if i.seg.end <= first.seg.start + 0.5 and i is not first]
    sung_before = [i for i in before if i.kind == "singing"]
    last_sung_end = max((i.seg.end for i in sung_before), default=0.0)
    kirtan_seconds = last_sung_end - start
    features.update(
        class_run_start=round(first.seg.start, 2),
        class_run_speech_s=round(_run_speech_s(class_run), 1),
        sung_segments_before=len(sung_before),
    )

    if not sung_before or kirtan_seconds < MIN_KIRTAN_S:
        has_marker = bool(marker_item)
        return Decision(
            "class_only",
            0.9 if has_marker or first.seg.start < 60 else 0.7,
            kirtan_start=start,
            class_start=first.seg.start,
            marker=marker_item.marker if marker_item else None,
            reasons=[
                "The recording starts with speech; there is no kirtan of at least "
                f"{int(MIN_KIRTAN_S)} seconds before it."
            ],
            features=features,
        )

    split_at, snapped = snap_to_silence(last_sung_end, silences, class_start)
    split_at = max(start + 1.0, min(split_at, first.seg.start))
    all_sung_text = " ".join(i.seg.text for i in sung_before)
    mantra = phrases.mantra_type(all_sung_text)
    sing_time = sum(i.seg.end - i.seg.start for i in before if i.kind == "singing")
    all_time = sum(i.seg.end - i.seg.start for i in before) or 1.0
    sing_share = sing_time / all_time

    score = 0.35
    reasons = [f"Singing until {last_sung_end:.1f}s, speech from {first.seg.start:.1f}s."]
    if marker_item:
        score += 0.25
        reasons.append(f'The class starts with the phrase "{marker_item.marker}".')
    if snapped:
        score += 0.15
        reasons.append("The cut is inside a silence.")
    score += 0.15 * sing_share
    score += 0.10 * min(1.0, _run_speech_s(class_run) / 60.0)
    lo, hi = PLAUSIBLE_KIRTAN_S
    if lo <= kirtan_seconds <= hi:
        score += 0.10
    else:
        score -= 0.2
        reasons.append(f"The kirtan is {kirtan_seconds / 60:.1f} minutes, which is unusual.")
    if mantra:
        score += 0.05
        reasons.append(f"The mantra is {mantra}.")
    else:
        reasons.append("The mantra type was not recognised; the kirtan is named Kirtan.")
    features.update(singing_share=round(sing_share, 3), kirtan_seconds=round(kirtan_seconds, 1))
    return Decision(
        "split",
        round(max(0.0, min(1.0, score)), 3),
        kirtan_start=start,
        split_at=round(split_at, 3),
        class_start=round(class_start, 3),
        mantra=mantra or phrases.MAHA_MANTRA,
        marker=marker_item.marker if marker_item else None,
        snapped_to_silence=snapped,
        reasons=reasons,
        features=features,
    )
