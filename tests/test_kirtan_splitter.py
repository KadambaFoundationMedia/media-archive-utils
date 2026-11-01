from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.audio_optimizer import probe
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.repo import register_original
from mam.helpers.transcriber import Segment
from mam.manager.tools import JobContext
from mam.tools.kirtan_splitter import phrases
from mam.tools.kirtan_splitter import tool as splitter
from mam.tools.kirtan_splitter.audio import extract_audio, find_silences
from mam.tools.kirtan_splitter.detect import detect
from tests.conftest import make_tone

SING = "hare krsna hare krsna krsna krsna hare hare"
TALK = "so we are all here to understand the nature of the soul and how it is different from matter"


def kirtan(until: float, step: float = 12.0, text: str = SING) -> list[Segment]:
    out, t = [], 0.0
    while t + step <= until:
        out.append(Segment(t, t + step - 1, text))
        t += step
    return out


def talk(
    start: float, seconds: float, step: float = 9.0, first: str | None = None
) -> list[Segment]:
    out, t = [], start
    while t + step <= start + seconds:
        out.append(Segment(t, t + step - 1, first if first and not out else TALK))
        t += step
    return out


# ------------------------------------------------------------------- phrases
def test_end_marker_and_mantra_types():
    assert phrases.end_marker("Om namo bhagavate vasudeva ajam") == "om namo bhagavate vasudeva"
    assert phrases.end_marker("Reading from Srimad Bhagavatam, canto three") is not None
    assert phrases.end_marker("it was a lovely evening") is None
    assert phrases.mantra_type("Jaya Radha Madhava, Jaya Kunja Bihari") == phrases.RADHA_MADHAVA
    assert phrases.mantra_type("Jaya jaya Sri Chaitanya, jaya Nityananda") == phrases.PANCA_TATTVA
    assert phrases.mantra_type("Namaste Narasimha, Prahladahladadayine") == phrases.NRSIMHA
    assert phrases.mantra_type("Hare Krishna Hare Krishna Krishna Krishna Hare Hare") == "Kirtan"
    assert phrases.mantra_type("so we were talking") is None


def test_time_round_trip():
    assert splitter.parse_time("28:13.048") == pytest.approx(1693.048)
    assert splitter.parse_time("1:02:03") == 3723
    assert splitter.parse_time("90.5") == 90.5
    assert splitter.format_time(1693.048) == "28:13.048"
    with pytest.raises(Exception, match="not a time"):
        splitter.parse_time("soon")


# ------------------------------------------------------------------- detection
def test_split_with_marker_is_confident():
    segs = kirtan(450) + talk(452, 120, first="Om namo bhagavate vasudeva ajam")
    d = detect(segs, 700, [(442, 452)])  # the last singing ends at 443
    assert d.mode == "split"
    assert d.snapped_to_silence
    assert 442 <= d.split_at <= 443
    assert d.marker == "om namo bhagavate vasudeva"
    assert d.score >= 0.9


def test_split_without_marker_or_silence_is_less_sure():
    d = detect(kirtan(450) + talk(480, 120), 700)
    assert d.mode == "split"
    assert d.marker is None
    assert d.score < 0.9
    assert abs(d.split_at - 443) <= 1.5


def test_mantra_is_named_in_the_decision():
    segs = kirtan(300, text="jaya radha madhava jaya kunja bihari jaya radha madhava") + talk(
        302, 120, first="reading from srimad bhagavatam"
    )
    assert detect(segs, 600).mantra == phrases.RADHA_MADHAVA


def test_singing_throughout_is_not_split():
    d = detect(kirtan(1800), 1800)
    assert d.mode == "kirtan_only"
    assert d.score >= 0.9


def test_class_without_kirtan_is_not_split():
    d = detect(talk(5, 300, first="Om namo bhagavate vasudeva ajam"), 400)
    assert d.mode == "class_only"
    assert d.score >= 0.9


def test_short_announcement_is_not_a_kirtan():
    d = detect(kirtan(40) + talk(42, 200), 400)
    assert d.mode == "class_only"


def test_empty_transcript_is_unclear():
    assert detect([], 600).mode == "unclear"


def test_unusually_long_kirtan_lowers_the_score():
    normal = detect(kirtan(450) + talk(452, 120, first="reading from srimad bhagavatam"), 700)
    long = detect(kirtan(3000) + talk(3002, 120, first="reading from srimad bhagavatam"), 3300)
    assert long.score < normal.score


def test_a_silent_looking_recording_keeps_its_start():
    d = detect(kirtan(450) + talk(452, 120), 700, [(0.0, 700.0)])
    assert d.kirtan_start == 0.0


def test_leading_silence_is_trimmed():
    d = detect(kirtan(450) + talk(452, 120), 700, [(0.0, 6.0)])
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
class FakeTranscriber:
    def __init__(self, segments):
        self.segments, self.calls = segments, 0

    def info(self):
        from mam.helpers.transcriber import TranscriptInfo

        return TranscriptInfo("fake", "1", "test", None, "cpu")

    def transcribe(self, audio):
        self.calls += 1
        return self.segments


@pytest.fixture
def prepared(settings, monkeypatch, reference, no_baserow):
    monkeypatch.setenv("SPOT_CHECK_RATE", "0")
    get_settings.cache_clear()
    settings = get_settings()

    def make(segments, name="2015-08-27_KKS_SB-3-6-6_se.mp3", seconds=300, ext="mp3"):
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
        fake = FakeTranscriber(segments)
        monkeypatch.setattr(splitter, "get_transcriber", lambda s=None: fake)
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
                }
            }
            record.status = "Renamer Done"
            rid = record.id
        return rid, copy, fake

    return make


def run(record_id, *, dry_run=False):
    with session_scope() as s:
        record = s.get(m.MediaFile, record_id)
        ctx = JobContext(1, s, get_settings(), dry_run, {}, record)
        return splitter.run_kirtan_splitter(ctx)


SEGS = kirtan(200, text="jaya radha madhava jaya kunja bihari") + talk(
    201, 90, first="om namo bhagavate vasudeva ajam"
)


def test_confident_split_writes_both_files(prepared):
    rid, copy, _ = prepared(SEGS)
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
    rid, copy, _ = prepared(SEGS)
    before = copy.stat().st_size
    outcome = run(rid, dry_run=True)
    assert outcome.state == "info" and "Jaya-radha-madhava" in outcome.summary
    assert copy.stat().st_size == before
    assert list(copy.parent.iterdir()) == [copy]
    with session_scope() as s:
        assert s.scalar(select(m.Review.id)) is None


def test_transcript_is_made_once(prepared):
    rid, _, fake = prepared(SEGS)
    run(rid, dry_run=True)
    run(rid, dry_run=True)
    assert fake.calls == 1


def test_unsure_split_opens_a_review_and_cuts_nothing(prepared):
    plain = kirtan(200) + talk(215, 90)  # no marker, no silence
    rid, copy, _ = prepared(plain)
    size = copy.stat().st_size
    outcome = run(rid)
    assert outcome.state == "review"
    assert copy.stat().st_size == size
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        assert review.tool == "kirtan-splitter" and review.outcome == "pending"
        assert review.proposal["mode"] == "split"


def test_approving_a_review_with_an_edited_cut(prepared):
    rid, copy, _ = prepared(kirtan(200) + talk(215, 90))
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
    rid, copy, _ = prepared(kirtan(200) + talk(215, 90))
    run(rid)
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        splitter.decide_review(
            s, review, action="no_split", split_at=None, mantra=None, operator="t", note=None
        )
        assert s.get(m.MediaFile, rid).status == "Skipped"
        assert review.outcome == "edited"  # the tool proposed a split


def test_singing_throughout_is_skipped(prepared):
    rid, copy, _ = prepared(kirtan(290))
    outcome = run(rid)
    assert outcome.state == "skipped"
    assert copy.exists()


def test_real_run_needs_the_renamed_copy(prepared):
    rid, copy, _ = prepared(SEGS)
    with session_scope() as s:
        s.get(m.MediaFile, rid).processed_path = None
    with pytest.raises(Exception, match="Renamer first"):
        run(rid)
    assert run(rid, dry_run=True).state == "info"  # a dry run may read the original


def test_cut_outside_processed_files_is_refused(prepared, tmp_path):
    rid, _, _ = prepared(SEGS)
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
    rid, _, _ = prepared(SEGS)
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

    rid, copy, _ = prepared(kirtan(200) + talk(215, 90))
    run(rid)
    with session_scope() as s:
        review_id = s.scalars(select(m.Review)).one().id
    with TestClient(create_app(start_queue=False)) as client:
        page = client.get(f"/review/{review_id}")
        assert page.status_code == 200
        assert "Kirtan splitter review" in page.text and "3:11.000" in page.text
        assert client.get("/reviews").status_code == 200
        bad = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "soon"},
            follow_redirects=False,
        )
        assert "not a time" in bad.headers["location"].replace("%20", " ")
        done = client.post(
            f"/review/{review_id}/decide",
            data={"action": "approve", "split_at": "3:11", "mantra": "Kirtan"},
            follow_redirects=False,
        )
        assert done.status_code == 303
    with session_scope() as s:
        review = s.get(m.Review, review_id)
        assert review.outcome == "approved" and review.final["split_at"] == 191
    assert probe(copy).duration == pytest.approx(300 - 191, abs=0.5)


def test_menu_lists_the_splitter(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    with TestClient(create_app(start_queue=False)) as client:
        assert "Kirtan splitter" in client.get("/").text


# ------------------------------------------------------- real samples (slow)
@pytest.mark.slow
def test_golden_samples(settings):
    """Needs trimming-samples/ and a transcription backend (run on the Mac, `pytest -m slow`)."""
    import yaml

    from mam.helpers.transcriber import get_transcriber
    from mam.tools.kirtan_splitter.audio import find_silences

    golden = yaml.safe_load((Path(__file__).parent / "fixtures" / "golden.yaml").read_text())
    root = Path(__file__).resolve().parent.parent
    try:
        transcriber = get_transcriber(settings)
    except Exception as exc:
        pytest.skip(str(exc))
    checked = 0
    for case in golden["kirtan_splitter"]:
        audio = root / case["file"]
        if not audio.exists():
            continue
        segments = transcriber.transcribe(audio)
        d = detect(segments, probe(audio).duration, find_silences(audio))
        assert d.mode == "split", f"{audio.name}: {d.reasons}"
        assert abs(d.split_at - case["kirtan_end"]) <= golden["tolerance_s"], audio.name
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
    rid, _, _ = prepared(SEGS)
    assert run(rid).state == "done"
    with session_scope() as s:
        kid = s.get(m.MediaFile, rid).meta["split"]["kirtan_id"]
        assert s.get(m.MediaFile, kid).baserow_row_id == 7
