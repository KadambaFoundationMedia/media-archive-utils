"""Local music/speech analysis: one score per second, from 0 (music) to 1 (speech).

Kirtan is sung with instruments: steady energy, few quiet moments, no syllable rhythm.
Speech has pauses between words and phrases, alternating voiced and unvoiced sounds, and an
energy envelope that pulses at the syllable rate (about 4 Hz). Three classic measures over a
3 second window, each mapped to 0..1 and averaged:

- low-energy share: fraction of 20 ms frames well below the window's average energy
- ZCR spread: how much the zero-crossing rate varies between frames
- 4 Hz modulation: share of the envelope's variation in the 2.5 to 6 Hz band

The thresholds below are starting values; tune them on real recordings with
`mam kirtan-split <file>` (the dry run prints a timeline of what was heard).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import binary

SAMPLE_RATE = 16000
FRAME = SAMPLE_RATE // 50  # 20 ms
FRAMES_PER_SECOND = 50
WINDOW_S = 3
SILENT_RMS = 10 ** (-60 / 20)  # a window quieter than -60 dBFS is silence

# (value that scores 0, value that scores 1)
LOW_ENERGY_RANGE = (0.15, 0.45)
ZCR_SPREAD_RANGE = (0.03, 0.10)
MODULATION_RANGE = (0.15, 0.40)
LOW_ENERGY_FRACTION = 0.3  # a frame below this share of the window's mean energy is "low"
BAND_HZ = (2.5, 6.0)


def _ramp(value: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return float(min(1.0, max(0.0, (value - lo) / (hi - lo))))


def frame_stats(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Energy and zero-crossing rate of every 20 ms frame (whole frames only)."""
    n = len(samples) // FRAME
    frames = samples[: n * FRAME].reshape(n, FRAME)
    energy = np.mean(frames**2, axis=1)
    signs = np.signbit(frames)
    zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)
    return energy, zcr


def window_score(energy: np.ndarray, zcr: np.ndarray) -> float | None:
    """Score for the frames of one window. None when the window is silent."""
    mean_energy = float(np.mean(energy))
    if mean_energy**0.5 < SILENT_RMS:
        return None
    low = float(np.mean(energy < LOW_ENERGY_FRACTION * mean_energy))
    spread = float(np.std(zcr))
    envelope = np.sqrt(energy)
    envelope = envelope - np.mean(envelope)
    spectrum = np.abs(np.fft.rfft(envelope * np.hanning(len(envelope)))) ** 2
    freqs = np.fft.rfftfreq(len(envelope), d=1 / FRAMES_PER_SECOND)
    total = float(np.sum(spectrum[1:])) or 1.0
    band = float(np.sum(spectrum[(freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])]))
    return (
        _ramp(low, LOW_ENERGY_RANGE)
        + _ramp(spread, ZCR_SPREAD_RANGE)
        + _ramp(band / total, MODULATION_RANGE)
    ) / 3


def profile_from_frames(energy: np.ndarray, zcr: np.ndarray) -> list[float | None]:
    """One score per second: a WINDOW_S window centred on each second."""
    seconds = len(energy) // FRAMES_PER_SECOND
    half = (WINDOW_S * FRAMES_PER_SECOND) // 2
    out: list[float | None] = []
    for sec in range(seconds):
        centre = sec * FRAMES_PER_SECOND + FRAMES_PER_SECOND // 2
        lo, hi = max(0, centre - half), min(len(energy), centre + half)
        out.append(window_score(energy[lo:hi], zcr[lo:hi]))
    return out


def profile_from_samples(samples: np.ndarray) -> list[float | None]:
    return profile_from_frames(*frame_stats(samples))


def speech_profile(path: Path) -> list[float | None]:
    """Decode `path` (mono, 16 kHz) as a stream and score it. Only the small per-frame
    statistics are kept, so a two-hour recording needs almost no memory."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path)]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    chunk = SAMPLE_RATE * 60 * 4  # one minute of float32; a multiple of FRAME samples
    energies: list[np.ndarray] = []
    zcrs: list[np.ndarray] = []
    pending = b""
    while True:
        raw = proc.stdout.read(chunk)
        if not raw:
            break
        pending += raw
        usable = len(pending) - len(pending) % (FRAME * 4)
        energy, zcr = frame_stats(np.frombuffer(pending[:usable], dtype=np.float32))
        energies.append(energy)
        zcrs.append(zcr)
        pending = pending[usable:]
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MamError(f"ffmpeg could not decode {path.name}: {err[-500:]}")
    if not energies:
        return []
    return profile_from_frames(np.concatenate(energies), np.concatenate(zcrs))
