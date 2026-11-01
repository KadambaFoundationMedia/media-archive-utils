from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.audio_optimizer import probe
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.repo import register_original
from mam.manager.tools import JobContext
from mam.tools.kirtan_splitter import phrases
from mam.tools.kirtan_splitter import tool as splitter
from mam.tools.kirtan_splitter.audio import extract_audio, find_silences
from mam.tools.kirtan_splitter.detect import detect
from mam.tools.kirtan_splitter.features import (
    SAMPLE_RATE,
    profile_from_samples,
    speech_profile,
)
from tests.conftest import make_tone


def profile(music: int, speech: int, music_value=0.01, speech_value=0.6):
    """A per-second profile: `music` seconds of music, then `speech` seconds of speech."""
    return [music_value] * music + [speech_value] * speech


# ------------------------------------------------------------------- phrases
def test_mantra_from_the_class():
    assert phrases.mantra_for_what("SB-3-6-6") == phrases.RADHA_MADHAVA
    assert phrases.mantra_for_what("BG-2-51") == phrases.RADHA_MADHAVA
    assert phrases.mantra_for_what("CC-Madhya-20-154") == phrases.PANCA_TATTVA
    assert phrases.mantra_for_what("Janmastami-evening-lecture") is None
    assert phrases.mantra_for_what(None) is None


def test_time_round_trip():
    assert splitter.parse_time("28:13.048") == pytest.approx(1693.048)
    assert splitter.parse_time("1:02:03") == 3723
    assert splitter.parse_time("90.5") == 90.5
    assert splitter.format_time(1693.048) == "28:13.048"
    with pytest.raises(Exception, match="not a time"):
        splitter.parse_time("soon")


# ------------------------------------------------------------------- detection
def test_split_in_a_silence_is_confident():
    d = detect(profile(450, 150), 600, [(448, 452)], phrases.RADHA_MADHAVA)
    assert d.mode == "split"
    assert d.snapped_to_silence and 448 <= d.split_at <= 449
    assert d.class_start == 450
    assert d.score >= 0.9


def test_split_without_silence_is_less_sure():
    d = detect(profile(450, 150), 600, [], phrases.RADHA_MADHAVA)
    assert d.mode == "split" and not d.snapped_to_silence
    assert d.score < 0.9
    assert d.split_at == pytest.approx(450, abs=1.5)


def test_mantra_defaults_to_kirtan_when_the_class_does_not_say():
    d = detect(profile(450, 150), 600, [(448, 452)])
    assert (
        d.mantra == "Kirtan"
        and d.score < detect(profile(450, 150), 600, [(448, 452)], phrases.RADHA_MADHAVA).score
    )


def test_music_throughout_is_not_split():
    d = detect([0.01] * 1800, 1800)
    assert d.mode == "kirtan_only" and d.score >= 0.9


def test_class_without_kirtan_is_not_split():
    d = detect(profile(0, 400), 400)
    assert d.mode == "class_only" and d.score >= 0.9


def test_short_announcement_is_not_a_kirtan():
    d = detect(profile(40, 400), 440)
    assert d.mode == "class_only"


def test_an_announcement_before_the_kirtan_does_not_hide_it():
    p = profile(0, 30) + profile(400, 200)
    d = detect(p, 630, [(0.0, 0.0)])
    assert d.mode == "split" and d.split_at == pytest.approx(430, abs=1.5)


def test_speech_blips_inside_the_kirtan_are_ignored():
    p = [0.01] * 200 + [0.6] * 8 + [0.01] * 200 + [0.6] * 150
    d = detect(p, 558)
    assert d.mode == "split" and d.split_at == pytest.approx(408, abs=1.5)


def test_alternating_music_and_speech_is_unclear():
    p = ([0.01] * 25 + [0.6] * 15) * 20
    assert detect(p, 800).mode in ("unclear", "split", "class_only")  # never a confident cut
    assert detect(p, 800).score < 0.9


def test_almost_no_sound_is_unclear():
    assert detect([None] * 600, 600).mode == "unclear"


def test_unusually_long_kirtan_lowers_the_score():
    normal = detect(profile(450, 150), 600, [(448, 452)], phrases.RADHA_MADHAVA)
    long = detect(profile(3000, 150), 3150, [(2998, 3002)], phrases.RADHA_MADHAVA)
    assert long.score < normal.score


def test_a_silent_looking_recording_keeps_its_start():
    assert detect(profile(450, 150), 600, [(0.0, 600.0)]).kirtan_start == 0.0


def test_leading_silence_is_trimmed():
    d = detect(profile(450, 150), 600, [(0.0, 6.0)])
    assert 5.5 <= d.kirtan_start <= 6.0


def test_timeline_shows_music_and_speech():
    d = detect(profile(100, 50), 150)
    assert d.features["timeline"] == "m" * 10 + "s" * 5


# ------------------------------------------------------------ the audio features
def test_the_speech_model_scores_every_second():
    t = np.arange(SAMPLE_RATE * 12) / SAMPLE_RATE
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    scores = profile_from_samples(tone)
    assert len(scores) == 12
    assert all(0.0 <= v <= 1.0 for v in scores)
    assert max(scores) < 0.1  # a pure tone is not speech


def test_the_model_ships_with_the_repository():
    from mam.tools.kirtan_splitter.features import MODEL

    assert MODEL.is_file() and MODEL.stat().st_size > 1_000_000
    assert (MODEL.parent / "LICENSE-silero-vad.txt").is_file()


def test_a_decoded_file_gets_one_score_per_second(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    tone = make_tone(tmp_path / "t.mp3", seconds=20, volume_db=-6)
    scores = speech_profile(tone)
    assert 18 <= len(scores) <= 21
    assert max(scores) < 0.1


# ------------------------------------------------------------------- ffmpeg
def test_find_silences(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    src = make_tone(tmp_path / "t.mp3", seconds=12, volume_db=-10)
    out = tmp_path / "gap.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
        + ["-af", "volume=enable='between(t,5,8)':volume=0", str(out)],
        check=True,
    )
    silences = find_silences(out)
    assert len(silences) == 1
    assert silences[0][0] == pytest.approx(5, abs=0.3)
    assert silences[0][1] == pytest.approx(8, abs=0.3)


def test_extract_audio_from_video(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
        + ["testsrc=size=64x48:rate=5:duration=3", "-f", "lavfi", "-i", "sine=duration=3"]
        + ["-c:v", "mpeg4", "-c:a", "aac", "-b:a", "128k", str(video)],
        check=True,
    )
    audio = extract_audio(video, tmp_path / "out" / "v", dry_run=False)
    assert audio.name == "v.mp3"
    assert probe(audio).codec == "mp3"
    assert probe(audio).duration == pytest.approx(3, abs=0.3)


# ----------------------------------------------------------- the whole job
@pytest.fixture
def prepared(settings, monkeypatch, reference, no_baserow):
    monkeypatch.setenv("SPOT_CHECK_RATE", "0")
    get_settings.cache_clear()
    settings = get_settings()

    def make(scripted, name="2015-08-27_KKS_SB-3-6-6_se.mp3", seconds=300, ext="mp3"):
        original = make_tone(
            settings.media_sample_files_path / f"orig.{ext}",
            seconds=seconds,
            volume_db=-6,
            codec_args=["-c:a", "libmp3lame", "-b:a", "32k"],
        )
        folder = settings.media_processed_files_path / "Srimad-bhagavatam"
        folder.mkdir(parents=True)
        copy = folder / name
        shutil.copy2(original, copy)
        monkeypatch.setattr(splitter, "speech_profile", lambda audio: scripted)
        with session_scope() as s:
            record = register_original(s, original)
            record.processed_path, record.current_name = str(copy), name
            record.meta = {
                "proposal": {
                    "when": "2015-08-27",
                    "what": "SB-3-6-6",
                    "where": "se",
                    "place": "Sweden",
                    "cc": "se",
                    "languages": [],
                    "title": "SB-3-6-6",
                }
            }
            record.status = "Renamer Done"
            rid = record.id
        return rid, copy

    return make


def run(record_id, *, dry_run=False):
    with session_scope() as s:
        record = s.get(m.MediaFile, record_id)
        ctx = JobContext(1, s, get_settings(), dry_run, {}, record)
        return splitter.run_kirtan_splitter(ctx)


# 190 s of music, then speech; no silence in the synthetic file, so the score is below 0.9
SURE = profile(190, 100)


@pytest.fixture
def sure_silence(monkeypatch):
    """Put a silence at the boundary, as a recording would have."""
    monkeypatch.setattr(splitter, "find_silences", lambda audio, duration=None: [(189.0, 192.0)])


def test_confident_split_writes_both_files(prepared, sure_silence):
    rid, copy = prepared(SURE)
    outcome = run(rid)
    assert outcome.state == "done", outcome.summary
    with session_scope() as s:
        record = s.get(m.MediaFile, rid)
        kirtan_rec = s.get(m.MediaFile, record.meta["split"]["kirtan_id"])
        kirtan_path = Path(kirtan_rec.processed_path)
        assert kirtan_path.name == "2015-08-27_KKS_Jaya-radha-madhava_se.mp3"
        assert kirtan_rec.parent_id == rid and kirtan_rec.derivation == "kirtan"
        assert kirtan_rec.status == "Processing complete"
        # the class keeps the combined copy's name and row
        assert record.current_name == "2015-08-27_KKS_SB-3-6-6_se.mp3"
        assert Path(record.processed_path) == copy
        cut = record.meta["split"]["split_at"]
    assert cut == pytest.approx(189, abs=1)
    assert probe(kirtan_path).duration == pytest.approx(cut, abs=0.5)
    assert probe(copy).duration == pytest.approx(300 - cut, abs=0.5)
    # the original is untouched
    original = get_settings().media_sample_files_path / "orig.mp3"
    assert probe(original).duration == pytest.approx(300, abs=0.5)


def test_dry_run_changes_nothing(prepared, sure_silence):
    rid, copy = prepared(SURE)
    before = copy.stat().st_size
    outcome = run(rid, dry_run=True)
    assert outcome.state == "info" and "Jaya-radha-madhava" in outcome.summary
    assert outcome.data["features"]["timeline"].startswith("mmmm")
    assert copy.stat().st_size == before
    assert list(copy.parent.iterdir()) == [copy]
    with session_scope() as s:
        assert s.scalar(select(m.Review.id)) is None


def test_the_splitter_does_not_transcribe():
    """Transcription belongs to the class-discoverer (Edwin, 2026-11-01)."""
    import mam.tools.kirtan_splitter as package

    for source in Path(package.__file__).parent.glob("*.py"):
        text = source.read_text().lower()
        assert "whisper" not in text and "get_transcriber" not in text, source.name


def test_unsure_split_opens_a_review_and_cuts_nothing(prepared):
    rid, copy = prepared(SURE)  # no silence at the cut: score under 0.9
    size = copy.stat().st_size
    outcome = run(rid)
    assert outcome.state == "review"
    assert copy.stat().st_size == size
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        assert review.tool == "kirtan-splitter" and review.outcome == "pending"
        assert review.proposal["mode"] == "split"


def test_approving_a_review_with_an_edited_cut(prepared):
    rid, copy = prepared(SURE)
    run(rid)
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        msg = splitter.decide_review(
            s,
            review,
            action="approve",
            split_at="3:30.5",
            mantra="Jaya-radha-madhava",
            operator="tester",
            note=None,
        )
        assert "3:30.500" in msg
        assert review.outcome == "edited" and review.final["split_at"] == 210.5
        record = s.get(m.MediaFile, rid)
        assert record.status == "Kirtan splitter Done"
    assert probe(copy).duration == pytest.approx(300 - 210.5, abs=0.5)


def test_do_not_split_decision(prepared):
    rid, copy = prepared(SURE)
    run(rid)
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        splitter.decide_review(
            s, review, action="no_split", split_at=None, mantra=None, operator="t", note=None
        )
        assert s.get(m.MediaFile, rid).status == "Skipped"
        assert review.outcome == "edited"  # the tool proposed a split


def test_music_throughout_is_skipped(prepared):
    rid, copy = prepared([0.01] * 290)
    outcome = run(rid)
    assert outcome.state == "skipped"
    assert copy.exists()


def test_real_run_needs_the_renamed_copy(prepared):
    rid, copy = prepared(SURE)
    with session_scope() as s:
        s.get(m.MediaFile, rid).processed_path = None
    with pytest.raises(Exception, match="Renamer first"):
        run(rid)
    assert run(rid, dry_run=True).state == "info"  # a dry run may read the original


def test_cut_outside_processed_files_is_refused(prepared):
    rid, _ = prepared(SURE)
    original = get_settings().media_sample_files_path / "orig.mp3"
    with session_scope() as s:
        record = s.get(m.MediaFile, rid)
        with pytest.raises(Exception, match="not a processed copy"):
            splitter.apply_split(
                s,
                record,
                original,
                {"split_at": 100.0, "mantra": "Kirtan"},
                get_settings(),
                dry_run=False,
            )


def test_kirtan_gets_its_own_baserow_row(prepared, sure_silence, no_baserow):
    rid, _ = prepared(SURE)
    run(rid)
    assert len(no_baserow) == 1
    values = no_baserow[0]["values"]
    assert values["what"] == "Jaya-radha-madhava" and values["baserow_row_id"] is None


def test_baserow_write_does_not_hold_the_database(prepared, sure_silence, monkeypatch):
    """Baserow writes log through their own session; ours must be released first."""
    from mam.helpers.local_db.repo import record_event

    def write_in_own_session(record, proposal, path, settings, dry_run, live=False):
        with session_scope() as other:  # would raise 'database is locked' if we held a write
            record_event(other, action="probe", tool="test", dry_run=True)
        return {"action": "create", "row_id": 7, "dry_run": True}

    monkeypatch.setattr("mam.tools.renamer.tool.write_baserow", write_in_own_session)
    rid, _ = prepared(SURE)
    assert run(rid).state == "done"
    with session_scope() as s:
        kid = s.get(m.MediaFile, rid).meta["split"]["kirtan_id"]
        assert s.get(m.MediaFile, kid).baserow_row_id == 7


def test_the_splitter_follows_the_renamer_in_the_pipeline():
    from mam.manager.tools import menu_tools, next_in_pipeline

    assert next_in_pipeline("renamer") == "kirtan-splitter"
    assert "kirtan-splitter" in [t.name for t in menu_tools()]


# ------------------------------------------------------------------ the portal
def test_review_page_and_approval_through_the_portal(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    rid, copy = prepared(SURE)
    run(rid)
    with session_scope() as s:
        review_id = s.scalars(select(m.Review)).one().id
    with TestClient(create_app(start_queue=False)) as client:
        page = client.get(f"/review/{review_id}")
        assert page.status_code == 200
        assert "Kirtan splitter review" in page.text and "3:10.000" in page.text
        assert client.get("/reviews").status_code == 200
        bad = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "soon"},
            follow_redirects=False,
        )
        assert "not a time" in bad.headers["location"].replace("%20", " ")
        done = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "3:10", "mantra": "Jaya-radha-madhava"},
            follow_redirects=False,
        )
        assert done.status_code == 303
    with session_scope() as s:
        review = s.get(m.Review, review_id)
        assert review.outcome == "approved" and review.final["split_at"] == 190
    assert probe(copy).duration == pytest.approx(300 - 190, abs=0.5)


def test_menu_lists_the_splitter(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    with TestClient(create_app(start_queue=False)) as client:
        assert "Kirtan splitter" in client.get("/").text


# ------------------------------------------------------- real samples (slow)
@pytest.mark.slow
def test_golden_samples(settings):
    """Needs trimming-samples/ (run on the Mac with `pytest -m slow`)."""
    import yaml

    golden = yaml.safe_load((Path(__file__).parent / "fixtures" / "golden.yaml").read_text())
    root = Path(__file__).resolve().parent.parent
    checked = 0
    for case in golden["kirtan_splitter"]:
        audio = root / case["file"]
        if not audio.exists():
            continue
        duration = probe(audio).duration
        d = detect(speech_profile(audio), duration, find_silences(audio, duration))
        assert d.mode == "split", f"{audio.name}: {d.reasons} {d.features.get('timeline')}"
        assert abs(d.split_at - case["kirtan_end"]) <= golden["tolerance_s"], audio.name
        checked += 1
    if not checked:
        pytest.skip("no trimming-samples on this machine")
