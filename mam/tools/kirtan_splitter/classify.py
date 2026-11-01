"""Tell singing from speech by the loudness pattern of the audio (no transcription).

Kirtan is sung without a break: its level stays steady and is never silent for long. A spoken
class has a lively level (words, stresses) and short pauses between sentences. Per window of a
few seconds we measure how much the 20 ms loudness varies and how much of the window is a pause,
all with numpy on audio decoded by ffmpeg, so it is fast and runs on macOS and Windows alike.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import binary

SAMPLE_RATE = 8000
FRAME_S = 0.02
WINDOW_S = 3.0
HOP_S = 1.0
SILENT_DB = -60.0  # a window whose loudest frames are quieter than this is silent
PAUSE_BELOW_PEAK_DB = 25.0  # a frame this far under the window's peak is a pause
SING_MAX_LEVEL_STD_DB = 4.5  # singing: the level varies less than this...
SING_MAX_PAUSE_SHARE = 0.05  # ...and has almost no pauses


@dataclass(frozen=True)
class Window:
    start: float
    kind: str  # singing | speech | silent
    level_std: float
    pause: float


def decode(path: Path) -> np.ndarray:
    """Mono float samples at SAMPLE_RATE (first audio stream)."""
    proc = subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1"]
        + ["-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise MamError(f"ffmpeg could not read {path.name}: {proc.stderr.decode()[-200:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def classify_samples(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[Window]:
    frame = int(FRAME_S * sample_rate)
    n = len(samples) // frame
    if n == 0:
        return []
    frames = samples[: n * frame].reshape(n, frame).astype(np.float64)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-12)
    per_window, per_hop = int(WINDOW_S / FRAME_S), int(HOP_S / FRAME_S)
    out: list[Window] = []
    for s in range(0, n - per_window + 1, per_hop):
        d = db[s : s + per_window]
        peak = float(np.percentile(d, 95))
        std = float(d.std())
        pause = float((d < peak - PAUSE_BELOW_PEAK_DB).mean())
        if peak < SILENT_DB:
            kind = "silent"
        elif std < SING_MAX_LEVEL_STD_DB and pause < SING_MAX_PAUSE_SHARE:
            kind = "singing"
        else:
            kind = "speech"
        out.append(Window(round(s * FRAME_S, 2), kind, round(std, 2), round(pause, 3)))
    return out


def classify(path: Path) -> list[Window]:
    return classify_samples(decode(path))
