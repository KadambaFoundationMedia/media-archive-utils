import subprocess

import pytest

from mam.errors import CorruptedMedia, ReadOnlyViolation
from mam.helpers.audio_optimizer import (
    TARGET_I,
    TARGET_TP,
    measure,
    normalize_whole,
    probe,
    stream_cut,
    verify_decode,
)


def test_probe(tone):
    info = probe(tone)
    assert (info.codec, info.sample_rate, info.channels) == ("mp3", 44100, 1)
    assert info.bitrate == 128000 and info.duration == pytest.approx(10, abs=0.1)
    assert not info.has_video


def test_corrupted_file_is_detected(settings):
    bad = settings.media_sample_files_path / "bad.mp3"
    bad.write_bytes(b"not audio at all" * 100)
    with pytest.raises(CorruptedMedia):
        probe(bad)


def test_verify_decode_clean(tone):
    assert verify_decode(tone) == []


def test_measure_reports_quiet_tone(tone):
    loud = measure(tone)
    assert loud.integrated < -25 and loud.true_peak < -25


def test_normalize_dry_run_writes_nothing(tone, settings):
    out = settings.media_processed_files_path / "n.mp3"
    result = normalize_whole(tone, out)
    assert result.dry_run and not out.exists()
    assert result.gain_db == pytest.approx(TARGET_I - result.before.integrated, abs=0.01)
    assert result.mode == "linear"


def test_normalize_hits_target_and_keeps_format(tone, settings):
    out = settings.media_processed_files_path / "n.mp3"
    result = normalize_whole(tone, out, dry_run=False)
    assert result.qa_passed, result.after
    assert abs(result.after.integrated - TARGET_I) <= 1 and result.after.true_peak <= TARGET_TP
    src, dst = probe(tone), probe(out)
    assert (dst.codec, dst.sample_rate, dst.channels) == (src.codec, src.sample_rate, src.channels)
    assert dst.bitrate == src.bitrate


def test_normalize_uses_limiting_when_peaks_are_too_high(settings):
    # Quiet speech-like level with one loud click: linear gain to -18 LUFS would clip.
    clip = settings.media_sample_files_path / "click.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.01*sin(2*PI*440*t)+if(between(t\\,5\\,5.005)\\,0.95\\,0):s=44100:d=10",
            "-c:a",
            "pcm_s16le",
            str(clip),
        ],
        check=True,
    )
    out = settings.media_processed_files_path / "click.wav"
    result = normalize_whole(clip, out, dry_run=False)
    assert result.mode == "dynamic"
    assert result.after.true_peak <= TARGET_TP


def test_normalize_refuses_to_write_into_originals(tone, settings):
    with pytest.raises(ReadOnlyViolation):
        normalize_whole(tone, settings.media_sample_files_path / "x.mp3", dry_run=False)


def test_stream_cut_keeps_ranges_without_reencoding(tone, settings):
    out = settings.media_processed_files_path / "cut.mp3"
    stream_cut(tone, out, [(1.0, 3.0), (6.0, None)], dry_run=False)
    info = probe(out)
    assert info.duration == pytest.approx(6.0, abs=0.15)  # 2 s + 4 s
    assert (info.codec, info.bitrate) == ("mp3", 128000)
    assert verify_decode(out) == []


def test_stream_cut_dry_run(tone, settings):
    out = settings.media_processed_files_path / "cut.mp3"
    commands = stream_cut(tone, out, [(0.0, 2.0)])
    assert len(commands) == 1 and "-c copy" in commands[0] and not out.exists()
