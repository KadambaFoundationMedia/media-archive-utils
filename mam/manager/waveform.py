"""What the waveform editor needs from the server: the peaks and audio the browser can play.

A 70 minute recording cannot be decoded in the browser, so the server decodes it once with ffmpeg
into 100 peaks per second (one byte each) and keeps them on the local disk. Formats a browser
cannot play (WMA, video) get a small mp3 preview, made once the same way. Both are only for
looking and listening: cuts are always made on the real file (stream copy).
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import threading
from pathlib import Path

import numpy as np

from mam.config import Settings
from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import binary

PEAKS_PER_SECOND = 100
DECODE_RATE = 8000  # Hz; plenty for a picture, 80 samples per peak
BROWSER_AUDIO = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac"}
_lock = threading.Lock()
_building: dict[str, threading.Lock] = {}


def _key(path: Path, suffix: str = "") -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{suffix}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _lock_for(key: str) -> threading.Lock:
    with _lock:
        return _building.setdefault(key, threading.Lock())


def peaks_from_samples(samples: np.ndarray, per_peak: int) -> np.ndarray:
    """Largest absolute value in each block of `per_peak` samples, as bytes (0 to 255)."""
    blocks = len(samples) // per_peak
    if blocks == 0:
        return np.zeros(0, dtype=np.uint8)
    top = np.abs(samples[: blocks * per_peak]).reshape(blocks, per_peak).max(axis=1)
    return np.round(np.minimum(top, 1.0) * 255).astype(np.uint8)


def compute_peaks(path: Path) -> bytes:
    """Decode `path` as a stream and return one byte per 1/PEAKS_PER_SECOND of audio."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path)]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(DECODE_RATE), "-f", "f32le", "-"]
    per_peak = DECODE_RATE // PEAKS_PER_SECOND
    step = per_peak * 4  # bytes in one peak
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    out = bytearray()
    pending = b""
    while raw := proc.stdout.read(step * 20000):  # about 40 s of audio at a time
        pending += raw
        usable = len(pending) - len(pending) % step
        samples = np.frombuffer(pending[:usable], dtype=np.float32)
        out += peaks_from_samples(samples, per_peak).tobytes()
        pending = pending[usable:]
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise MamError(f"ffmpeg could not decode {path.name}: {err[-300:]}")
    return bytes(out)


def peaks_for(path: Path, settings: Settings) -> dict:
    """The peaks of `path` for wavesurfer.js: {per_second, duration, peaks (base64 bytes)}."""
    key = _key(path, "peaks")
    store = settings.work_dir / "waveform" / f"{key}.peaks"
    with _lock_for(key):
        if store.exists():
            data = store.read_bytes()
        else:
            data = compute_peaks(path)
            store.parent.mkdir(parents=True, exist_ok=True)
            tmp = store.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(store)
    return {
        "per_second": PEAKS_PER_SECOND,
        "duration": len(data) / PEAKS_PER_SECOND,
        "peaks": base64.b64encode(data).decode("ascii"),
    }


def playable(path: Path, settings: Settings) -> Path:
    """`path` itself when a browser plays it, otherwise a small mp3 made from it (once)."""
    if path.suffix.lower() in BROWSER_AUDIO:
        return path
    key = _key(path, "preview")
    preview = settings.work_dir / "waveform" / f"{key}.mp3"
    with _lock_for(key):
        if not preview.exists():
            preview.parent.mkdir(parents=True, exist_ok=True)
            tmp = preview.with_suffix(".part.mp3")
            cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)]
            cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "22050", "-b:a", "64k"]
            cmd += [str(tmp)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise MamError(f"no preview of {path.name}: {proc.stderr[-300:]}")
            tmp.replace(preview)
    return preview
