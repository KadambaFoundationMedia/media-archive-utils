"""Local speech detection: one score per second, from 0 (no speech) to 1 (speech).

Uses the Silero voice-activity model (MIT licence, see `models/LICENSE-silero-vad.txt`), run
with onnxruntime on the CPU, so it works the same on macOS and Windows. The 2.3 MB model file
ships with the repository: nothing is downloaded and no network is needed.

Kirtan (singing with instruments) scores near 0 and a class (speech) scores well above it.
On the five trimming samples the kirtan averages 0.00 to 0.01 and the class 0.41 to 0.65, so
the splitter treats a second as speech from `detect.SPEECH_AT`.

The voice model cannot see where the music stops (a sung prayer or a quiet room scores 0, like
the kirtan), so `sound_profile` adds the level and the share of high frequencies per second;
`detect.music_end` uses them to find where the kirtan really ends.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import binary

MODEL = Path(__file__).parent / "models" / "silero_vad.onnx"
SAMPLE_RATE = 16000
CHUNK = 512  # samples per model step (32 ms)
CONTEXT = 64
CHUNKS_PER_SECOND = SAMPLE_RATE / CHUNK  # 31.25


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    return ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"], sess_options=opts)


class _Model:
    """The model with its running state, fed 512 samples at a time."""

    def __init__(self) -> None:
        self.session = _session()
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT), dtype=np.float32)
        self.sr = np.array(SAMPLE_RATE, dtype=np.int64)

    def __call__(self, chunk: np.ndarray) -> float:
        data = np.concatenate([self.context, chunk[None, :]], axis=1)
        prob, self.state = self.session.run(
            None, {"input": data, "state": self.state, "sr": self.sr}
        )
        self.context = data[:, -CONTEXT:]
        return float(prob[0, 0])


def probabilities_to_profile(probs: list[float]) -> list[float | None]:
    """Average the 32 ms probabilities into one value per second."""
    seconds = int(len(probs) / CHUNKS_PER_SECOND)
    return [
        float(np.mean(probs[int(s * CHUNKS_PER_SECOND) : int((s + 1) * CHUNKS_PER_SECOND)]))
        for s in range(seconds)
    ]


def profile_from_samples(samples: np.ndarray) -> list[float | None]:
    """Profile of mono float32 samples at 16 kHz (used by tests and short clips)."""
    model = _Model()
    n = len(samples) // CHUNK
    return probabilities_to_profile([model(samples[i * CHUNK : (i + 1) * CHUNK]) for i in range(n)])


def speech_profile(path: Path) -> list[float | None]:
    """Decode `path` (mono, 16 kHz) as a stream and score it second by second."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path)]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    model = _Model()
    probs: list[float] = []
    step = CHUNK * 4  # bytes of float32
    pending = b""
    while True:
        raw = proc.stdout.read(step * 2000)  # about a minute at a time
        if not raw:
            break
        pending += raw
        usable = len(pending) - len(pending) % step
        samples = np.frombuffer(pending[:usable], dtype=np.float32)
        probs.extend(model(samples[i : i + CHUNK]) for i in range(0, len(samples), CHUNK))
        pending = pending[usable:]
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MamError(f"ffmpeg could not decode {path.name}: {err[-500:]}")
    return probabilities_to_profile(probs)


HIGH_BAND_HZ = 2000  # cymbals, kartals and drums live above this; a speaking voice mostly below
SILENCE_DB = -120.0


@dataclass
class Sound:
    """Per second: loudness in dBFS (RMS) and the share of the energy above HIGH_BAND_HZ."""

    level: list[float]
    high: list[float]


def sound_from_samples(samples: np.ndarray) -> Sound:
    """Level and high-band share per whole second of mono 16 kHz float32 samples."""
    seconds = len(samples) // SAMPLE_RATE
    if seconds == 0:
        return Sound([], [])
    frames = samples[: seconds * SAMPLE_RATE].reshape(seconds, SAMPLE_RATE)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    level = 20 * np.log10(np.maximum(rms, 1e-6))
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2  # one bin per hertz
    total = power.sum(axis=1)
    high = np.divide(
        power[:, HIGH_BAND_HZ:].sum(axis=1), total, out=np.zeros(seconds), where=total > 0
    )
    return Sound([float(v) for v in level], [float(v) for v in high])


def sound_profile(path: Path) -> Sound:
    """Decode `path` (mono, 16 kHz) as a stream and measure it second by second."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path)]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    level: list[float] = []
    high: list[float] = []
    step = SAMPLE_RATE * 4  # bytes in one second
    pending = b""
    while True:
        raw = proc.stdout.read(step * 120)
        if not raw:
            break
        pending += raw
        usable = len(pending) - len(pending) % step
        part = sound_from_samples(np.frombuffer(pending[:usable], dtype=np.float32))
        level.extend(part.level)
        high.extend(part.high)
        pending = pending[usable:]
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MamError(f"ffmpeg could not decode {path.name}: {err[-500:]}")
    return Sound(level, high)
