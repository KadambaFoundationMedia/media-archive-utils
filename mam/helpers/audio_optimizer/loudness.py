"""Whole-recording loudness: measure and normalize to the approved speech target.

Target (helpers/audio-optimizer/SPECS.md): -18 LUFS +/-1 LU integrated, -1 dBTP ceiling.
Output keeps the source codec, bitrate, sample rate and channel count (one re-encode).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from mam.errors import MamError
from mam.helpers.audio_optimizer.ffmpeg import AudioInfo, binary, probe, run
from mam.helpers.file_organizer.organizer import assert_writable

log = logging.getLogger(__name__)

TARGET_I = -18.0
TARGET_TP = -1.0
TOLERANCE_LU = 1.0
# Lossy encoders overshoot true peak slightly; aim lower so the encoded file stays under -1 dBTP.
ENCODER_HEADROOM_DB = 0.5
# The sample-peak limiter lets a little through between samples; aim slightly lower.
LIMITER_MARGIN_DB = 0.5
# Re-encode once more when limiting left the result further than this from the target.
CORRECTION_LU = 0.3
MAX_PASSES = 2

# codec_name from ffprobe -> ffmpeg encoder
ENCODERS = {
    "mp3": "libmp3lame",
    "aac": "aac",
    "wmav2": "wmav2",
    "wmav1": "wmav2",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "alac": "alac",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
}
LOSSLESS = {"flac", "alac", "pcm_s16le", "pcm_s24le"}


@dataclass(frozen=True)
class Loudness:
    integrated: float  # LUFS
    true_peak: float  # dBTP
    lra: float  # LU
    threshold: float

    def meets_target(self, target_i: float = TARGET_I, target_tp: float = TARGET_TP) -> bool:
        return abs(self.integrated - target_i) <= TOLERANCE_LU and self.true_peak <= target_tp


@dataclass
class NormalizeResult:
    src: Path
    dst: Path
    dry_run: bool
    before: Loudness
    after: Loudness | None
    gain_db: float
    mode: str  # linear (pure gain) | dynamic (gain + true-peak limiter)
    encoder: str
    qa_passed: bool | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["src"], d["dst"] = str(self.src), str(self.dst)
        return d


def _gain_filter(gain_db: float, ceiling_db: float, sample_rate: int, linear: bool) -> str:
    """Pure gain, or gain followed by a peak limiter run at 4x the sample rate so that
    inter-sample (true) peaks are caught. The limiter only touches peaks above the ceiling."""
    if linear:
        return f"volume={gain_db}dB"
    limit = 10 ** ((ceiling_db - LIMITER_MARGIN_DB) / 20)
    return (
        f"volume={gain_db}dB,aresample={sample_rate * 4},"
        f"alimiter=limit={limit:.4f}:level=false:attack=5:release=50,"
        f"aresample={sample_rate}"
    )


def _encode(src: Path, dst: Path, audio_filter: str, enc_args: list[str]) -> None:
    tmp = dst.with_name(f".{dst.stem}.part{dst.suffix}")
    try:
        run(
            [
                binary("ffmpeg"),
                "-hide_banner",
                "-nostats",
                "-y",
                "-i",
                str(src),
                "-map",
                "0:a:0",
                "-map_metadata",
                "0",
                "-vn",
                "-af",
                audio_filter,
                *enc_args,
                str(tmp),
            ]
        )
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def measure(path: Path) -> Loudness:
    """Integrated loudness, true peak and loudness range (ffmpeg ebur128, ~4x faster than
    a loudnorm analysis pass with identical results)."""
    proc = run(
        [
            binary("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true:framelog=quiet",
            "-f",
            "null",
            "-",
        ]
    )
    summary = proc.stderr[proc.stderr.rfind("Summary:") :]

    def value(label: str) -> float:
        match = re.search(rf"{label}:\s+(-?[\d.]+|-inf)", summary)
        if not match:
            raise MamError(f"Could not parse '{label}' from ebur128 output")
        return float(match.group(1))

    return Loudness(
        integrated=value("I"),
        true_peak=value("Peak"),
        lra=value("LRA"),
        threshold=value("Threshold"),
    )


def encoder_args(info: AudioInfo) -> tuple[str, list[str]]:
    encoder = ENCODERS.get(info.codec)
    if encoder is None:
        raise MamError(f"No encoder configured for source codec '{info.codec}'")
    args = ["-c:a", encoder, "-ar", str(info.sample_rate), "-ac", str(info.channels)]
    if info.codec not in LOSSLESS and info.bitrate:
        args += ["-b:a", f"{round(info.bitrate / 1000)}k"]
    return encoder, args


def normalize_whole(
    src: Path,
    dst: Path,
    *,
    target_i: float = TARGET_I,
    target_tp: float = TARGET_TP,
    dry_run: bool = True,
) -> NormalizeResult:
    """Normalize to the speech target, re-encoded once at the source format.

    Uses a pure linear gain when the true peak allows it. Otherwise (`mode="dynamic"`) the gain
    is followed by an oversampled peak limiter, with one corrective pass if limiting cost
    loudness. Callers should surface dynamic results for review.
    """
    info = probe(src)
    encoder, enc_args = encoder_args(info)
    aim_tp = target_tp - (0 if info.codec in LOSSLESS else ENCODER_HEADROOM_DB)
    before = measure(src)
    gain = round(target_i - before.integrated, 2)
    linear = before.true_peak + gain <= aim_tp
    mode = "linear" if linear else "dynamic"
    result = NormalizeResult(src, dst, dry_run, before, None, gain, mode, encoder, None)
    if dry_run:
        return result

    assert_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(MAX_PASSES):
        _encode(src, dst, _gain_filter(gain, aim_tp, info.sample_rate, linear), enc_args)
        result.after = measure(dst)
        deficit = target_i - result.after.integrated
        if linear or abs(deficit) <= CORRECTION_LU:
            break
        # The limiter shaved some loudness off the peaks; add it back once.
        gain = round(gain + deficit, 2)
    result.gain_db = gain
    result.qa_passed = result.after.meets_target(target_i, target_tp)
    log.info(
        "Normalized %s: %.1f -> %.1f LUFS, TP %.1f dBTP (%s, QA %s)",
        src.name,
        before.integrated,
        result.after.integrated,
        result.after.true_peak,
        result.mode,
        "passed" if result.qa_passed else "FAILED",
    )
    return result
