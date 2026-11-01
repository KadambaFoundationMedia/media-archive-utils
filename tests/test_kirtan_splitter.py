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
from mam.tools.kirtan_splitter.classify import SAMPLE_RATE, Window, classify_samples
from mam.tools.kirtan_splitter.detect import detect
from tests.conftest import make_tone


def wins(*parts: tuple[str, float]) -> list[Window]:
    """Windows (1 s hop) for consecutive stretches: ("singing", 200), ("silent", 5), ..."""
    out, t = [], 0.0
    for kind, seconds in parts:
        end = t + seconds
        while t + 3 <= end:
            out.append(Window(t, kind, 1.0, 0.0))
            t += 1.0
        t = end
    return out


# ------------------------------------------------------------------- mantra
def test_mantra_follows_the_scripture():
    assert phrases.mantra_for("SB-3-6-6", None) == phrases.RADHA_MADHAVA
    assert phrases.mantra_for("BG-2-51", None) == phrases.RADHA_MADHAVA
    assert phrases.mantra_for("CC-Madhya-20-154", None) == phrases.PANCA_TATTVA
    assert phrases.mantra_for(None, "Srimad-bhagavatam") == phrases.RADHA_MADHAVA
    assert phrases.mantra_for("Janmastami-evening-lecture", "Festival") is None


def test_time_round_trip():
    assert splitter.parse_time("28:13.048") == pytest.approx(1693.048)
    assert splitter.parse_time("1:02:03") == 3723
    assert splitter.parse_time("90.5") == 90.5
    assert splitter.format_time(1693.048) == "28:13.048"
    with pytest.raises(Exception, match="not a time"):
        splitter.parse_time("soon")


# ------------------------------------------------------------------- classifier
def _tone(seconds: float, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _talk(seconds: float) -> np.ndarray:
    """Bursts of noise with pauses and uneven levels, like sentences."""
    rng = np.random.default_rng(1)
    out = []
    while sum(len(x) for x in out) < seconds * SAMPLE_RATE:
        burst = rng.normal(0, rng.uniform(0.02, 0.2), int(rng.uniform(0.4, 1.2) * SAMPLE_RATE))
        out += [burst.astype(np.float32), np.zeros(int(0.35 * SAMPLE_RATE), np.float32) + 1e-4]
    return np.concatenate(out)[: int(seconds * SAMPLE_RATE)]


def test_classifier_tells_steady_singing_from_talk_and_silence():
    audio = np.concatenate([_tone(20), _talk(20), np.zeros(10 * SAMPLE_RATE, np.float32)])
    kinds = {w.start: w.kind for w in classify_samples(audio)}
    assert kinds[5.0] == "singing"
    assert kinds[25.0] == "speech"
    assert kinds[42.0] == "silent"


# ------------------------------------------------------------------- detection
SILENCE = [(199.5, 202.0)]


def test_split_in_a_silence_is_confident():
    d = detect(wins(("singing", 200), ("silent", 4), ("speech", 120)), 400, SILENCE)
    assert d.mode == "split"
    assert d.snapped_to_silence
    assert d.split_at == pytest.approx(200, abs=1)
    assert d.score >= 0.9


def test_split_without_silence_is_less_sure():
    d = detect(wins(("singing", 200), ("silent", 12), ("speech", 120)), 400)
    assert d.mode == "split"
    assert d.score < 0.9
    assert abs(d.split_at - 200) <= 1.5


def test_the_mantra_is_passed_to_the_decision():
    d = detect(wins(("singing", 200), ("speech", 120)), 400, mantra=phrases.RADHA_MADHAVA)
    assert d.mantra == phrases.RADHA_MADHAVA
    assert detect(wins(("singing", 200), ("speech", 120)), 400).mantra == "Kirtan"


def test_singing_throughout_is_not_split():
    d = detect(wins(("singing", 1800)), 1800)
    assert d.mode == "kirtan_only"
    assert d.score >= 0.9


def test_class_without_kirtan_is_not_split():
    d = detect(wins(("silent", 3), ("speech", 300)), 400)
    assert d.mode == "class_only"
    assert d.score >= 0.9


def test_short_announcement_is_not_a_kirtan():
    d = detect(wins(("singing", 40), ("speech", 200)), 400)
    assert d.mode == "class_only"


def test_speech_inside_the_kirtan_is_not_the_class():
    parts = (("singing", 100), ("speech", 12), ("singing", 100), ("speech", 120))
    d = detect(wins(*parts), 400)
    assert d.mode == "split" and d.split_at == pytest.approx(212, abs=1)


def test_no_audio_is_unclear():
    assert detect([], 600).mode == "unclear"
    assert detect(wins(("silent", 300)), 300).mode == "unclear"


def test_unusually_long_kirtan_lowers_the_score():
    normal = detect(wins(("singing", 450), ("speech", 120)), 700)
    long = detect(wins(("singing", 3000), ("speech", 120)), 3300)
    assert long.score < normal.score


def test_a_silent_looking_recording_keeps_its_start():
    d = detect(wins(("singing", 450), ("speech", 120)), 700, [(0.0, 700.0)])
    assert d.kirtan_start == 0.0


def test_leading_silence_is_trimmed():
    d = detect(wins(("singing", 450), ("speech", 120)), 700, [(0.0, 6.0)])
    assert 5.5 <= d.kirtan_start <= 6.0


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

    def make(windows, name="2015-08-27_KKS_SB-3-6-6_se.mp3", seconds=300, silences=()):
        original = make_tone(
            settings.media_sample_files_path / "orig.mp3",
            seconds=seconds,
            volume_db=-6,
            codec_args=["-c:a", "libmp3lame", "-b:a", "32k"],
        )
        folder = settings.media_processed_files_path / "Srimad-bhagavatam"
        folder.mkdir(parents=True)
        copy = folder / name
        shutil.copy2(original, copy)
        calls = []
        monkeypatch.setattr(splitter, "classify", lambda audio: calls.append(audio) or windows)
        monkeypatch.setattr(splitter, "find_silences", lambda audio, duration=None: list(silences))
        with session_scope() as s:
            record = register_original(s, original)
            record.processed_path, record.current_name = str(copy), name
            record.meta = {
                "proposal": {
                    "when": "2015-08-27",
                    "where": "se",
                    "place": "Sweden",
                    "cc": "se",
                    "languages": [],
                    "title": "SB-3-6-6",
                    "what": "SB-3-6-6",
                }
            }
            record.status = "Renamer Done"
            rid = record.id
        return rid, copy, calls

    return make


def run(record_id, *, dry_run=False):
    with session_scope() as s:
        record = s.get(m.MediaFile, record_id)
        ctx = JobContext(1, s, get_settings(), dry_run, {}, record)
        return splitter.run_kirtan_splitter(ctx)


SEGS = wins(("singing", 200), ("silent", 3), ("speech", 90))
QUIET = [(199.5, 202.5)]
UNSURE = wins(("singing", 200), ("silent", 12), ("speech", 78))


def test_confident_split_writes_both_files(prepared):
    rid, copy, _ = prepared(SEGS, silences=QUIET)
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
    assert probe(kirtan_path).duration == pytest.approx(cut, abs=0.5)
    assert probe(copy).duration == pytest.approx(300 - cut, abs=0.5)
    # the original is untouched
    assert probe(get_settings().media_sample_files_path / "orig.mp3").duration == pytest.approx(
        300, abs=0.5
    )


def test_dry_run_changes_nothing(prepared):
    rid, copy, _ = prepared(SEGS, silences=QUIET)
    before = copy.stat().st_size
    outcome = run(rid, dry_run=True)
    assert outcome.state == "info" and "Jaya-radha-madhava" in outcome.summary
    assert copy.stat().st_size == before
    assert list(copy.parent.iterdir()) == [copy]
    with session_scope() as s:
        assert s.scalar(select(m.Review.id)) is None


def test_nothing_is_transcribed(prepared):
    """The splitter listens to the audio itself: it needs no transcript."""
    import mam.tools.kirtan_splitter.tool as tool_module

    assert not hasattr(tool_module, "get_transcriber")
    rid, _, calls = prepared(SEGS, silences=QUIET)
    assert run(rid, dry_run=True).state == "info"
    assert len(calls) == 1


def test_unsure_split_opens_a_review_and_cuts_nothing(prepared):
    rid, copy, _ = prepared(UNSURE)
    size = copy.stat().st_size
    outcome = run(rid)
    assert outcome.state == "review"
    assert copy.stat().st_size == size
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        assert review.tool == "kirtan-splitter" and review.outcome == "pending"
        assert review.proposal["mode"] == "split"


def test_approving_a_review_with_an_edited_cut(prepared):
    rid, copy, _ = prepared(UNSURE)
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
    rid, copy, _ = prepared(UNSURE)
    run(rid)
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        splitter.decide_review(
            s, review, action="no_split", split_at=None, mantra=None, operator="t", note=None
        )
        assert s.get(m.MediaFile, rid).status == "Skipped"
        assert review.outcome == "edited"  # the tool proposed a split


def test_singing_throughout_is_skipped(prepared):
    rid, copy, _ = prepared(wins(("singing", 290)))
    outcome = run(rid)
    assert outcome.state == "skipped"
    assert copy.exists()


def test_real_run_needs_the_renamed_copy(prepared):
    rid, copy, _ = prepared(SEGS, silences=QUIET)
    with session_scope() as s:
        s.get(m.MediaFile, rid).processed_path = None
    with pytest.raises(Exception, match="Renamer first"):
        run(rid)
    assert run(rid, dry_run=True).state == "info"  # a dry run may read the original


def test_cut_outside_processed_files_is_refused(prepared, tmp_path):
    rid, _, _ = prepared(SEGS, silences=QUIET)
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


def test_kirtan_gets_its_own_baserow_row(prepared, no_baserow):
    rid, _, _ = prepared(SEGS, silences=QUIET)
    run(rid)
    assert len(no_baserow) == 1
    values = no_baserow[0]["values"]
    assert values["what"] == "Jaya-radha-madhava" and values["baserow_row_id"] is None


def test_the_splitter_follows_the_renamer_in_the_pipeline():
    from mam.manager.tools import menu_tools, next_in_pipeline

    assert next_in_pipeline("renamer") == "kirtan-splitter"
    assert "kirtan-splitter" in [t.name for t in menu_tools()]


# ------------------------------------------------------------------ the portal
def test_review_page_and_approval_through_the_portal(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    rid, copy, _ = prepared(UNSURE)
    run(rid)
    with session_scope() as s:
        review_id = s.scalars(select(m.Review)).one().id
    with TestClient(create_app(start_queue=False)) as client:
        page = client.get(f"/review/{review_id}")
        assert page.status_code == 200
        assert "Kirtan splitter review" in page.text and "3:20.000" in page.text
        assert client.get("/reviews").status_code == 200
        bad = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "soon"},
            follow_redirects=False,
        )
        assert "not a time" in bad.headers["location"].replace("%20", " ")
        done = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "3:20", "mantra": "Jaya-radha-madhava"},
            follow_redirects=False,
        )
        assert done.status_code == 303
    with session_scope() as s:
        review = s.get(m.Review, review_id)
        assert review.outcome == "approved" and review.final["split_at"] == 200
    assert probe(copy).duration == pytest.approx(300 - 200, abs=0.5)


def test_menu_lists_the_splitter(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    with TestClient(create_app(start_queue=False)) as client:
        assert "Kirtan splitter" in client.get("/").text


# ------------------------------------------------------- real samples (slow)
@pytest.mark.slow
def test_golden_samples(settings):
    """Needs trimming-samples/ (run on the Mac, `pytest -m slow`).

    The audio alone finds the end of the kirtan only roughly: the documented times follow the
    speaker's words. Every sample must be found as a split with the cut within 60 s of the
    documented one; the exact cut is settled on the review page.
    """
    import yaml

    from mam.tools.kirtan_splitter.classify import classify

    golden = yaml.safe_load((Path(__file__).parent / "fixtures" / "golden.yaml").read_text())
    root = Path(__file__).resolve().parent.parent
    checked = 0
    for case in golden["kirtan_splitter"]:
        audio = root / case["file"]
        if not audio.exists():
            continue
        d = detect(classify(audio), probe(audio).duration, find_silences(audio))
        assert d.mode == "split", f"{audio.name}: {d.reasons}"
        assert abs(d.split_at - case["kirtan_end"]) <= 60, audio.name
        checked += 1
    if not checked:
        pytest.skip("no trimming-samples on this machine")


def test_baserow_write_does_not_hold_the_database(prepared, monkeypatch):
    """Baserow writes log through their own session; ours must be released first."""
    from mam.helpers.local_db.repo import record_event

    def write_in_own_session(record, proposal, path, settings, dry_run, live=False):
        with session_scope() as other:  # would raise 'database is locked' if we held a write
            record_event(other, action="probe", tool="test", dry_run=True)
        return {"action": "create", "row_id": 7, "dry_run": True}

    monkeypatch.setattr("mam.tools.renamer.tool.write_baserow", write_in_own_session)
    rid, _, _ = prepared(SEGS, silences=QUIET)
    assert run(rid).state == "done"
    with session_scope() as s:
        kid = s.get(m.MediaFile, rid).meta["split"]["kirtan_id"]
        assert s.get(m.MediaFile, kid).baserow_row_id == 7
