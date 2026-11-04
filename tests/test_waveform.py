from __future__ import annotations

import base64
import shutil

import numpy as np
import pytest

from mam.manager import waveform
from tests.conftest import make_tone


def test_peaks_are_the_largest_value_of_each_block():
    samples = np.array([0.0, 0.5, -1.0, 0.25, 0.1, -0.1, 2.0], dtype=np.float32)
    # 7 samples in blocks of 2: three whole blocks; the loudest is clipped to 255
    assert waveform.peaks_from_samples(samples, 2).tolist() == [128, 255, 26]
    assert waveform.peaks_from_samples(samples[:1], 2).tolist() == []


def test_peaks_of_a_file(tmp_path, settings):
    tone = make_tone(tmp_path / "t.mp3", seconds=12, volume_db=-6)  # amplitude about 0.5
    data = waveform.peaks_for(tone, settings)
    peaks = np.frombuffer(base64.b64decode(data["peaks"]), dtype=np.uint8)
    assert data["per_second"] == 100
    assert data["duration"] == pytest.approx(12, abs=0.3)
    assert len(peaks) == round(data["duration"] * 100)
    assert 12 <= np.median(peaks) <= 20  # a sine at 1/8 of full scale, minus 6 dB: about 0.0625
    # kept on disk: a second call does not decode again
    kept = list((settings.work_dir / "waveform").glob("*.peaks"))
    assert len(kept) == 1
    kept[0].write_bytes(bytes([7] * 50))
    assert waveform.peaks_for(tone, settings)["duration"] == 0.5


def test_a_browser_format_is_played_as_it_is(tmp_path, settings):
    tone = make_tone(tmp_path / "t.mp3", seconds=3)
    assert waveform.playable(tone, settings) == tone


def test_wma_gets_a_small_mp3_preview(tmp_path, settings):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    wma = make_tone(tmp_path / "t.wma", seconds=4, codec_args=["-c:a", "wmav2"])
    preview = waveform.playable(wma, settings)
    assert preview.suffix == ".mp3" and preview.exists() and preview != wma
    assert waveform.playable(wma, settings) == preview  # made once
