"""Audio optimizer helper, part 1: probing, loudness and lossless cuts.

Audience-question detection and interval gain come with the class-trimmer phase.
"""

from mam.helpers.audio_optimizer.cutting import stream_cut
from mam.helpers.audio_optimizer.ffmpeg import AudioInfo, probe, verify_decode
from mam.helpers.audio_optimizer.loudness import (
    TARGET_I,
    TARGET_TP,
    Loudness,
    NormalizeResult,
    measure,
    normalize_whole,
)

__all__ = [
    "TARGET_I",
    "TARGET_TP",
    "AudioInfo",
    "Loudness",
    "NormalizeResult",
    "measure",
    "normalize_whole",
    "probe",
    "stream_cut",
    "verify_decode",
]
