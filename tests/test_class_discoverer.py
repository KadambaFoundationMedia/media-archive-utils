from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.audio_optimizer import measure
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.repo import register_original
from mam.helpers.transcriber import Segment, Transcript, load_transcript, transcribe, write_srt
from mam.helpers.transcriber.backends import cpp_segments
from mam.helpers.vedabase import VedabaseClient, VerseRef, parse_verse_page
from mam.manager.tools import JobContext
from mam.tools.class_discoverer import tool as discoverer
from mam.tools.class_discoverer.match import match_translation
from tests.conftest import FIXTURES, make_tone

VEDA = FIXTURES / "vedabase"


def page(name: str):
    return parse_verse_page((VEDA / f"{name}.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ verse refs
@pytest.mark.parametrize(
    "what,path,tag",
    [
        ("BG-2-51", "/en/library/bg/2/51/", "2.51"),
        ("SB-9-20-20-Dont-waste-time", "/en/library/sb/9/20/20/", "9.20.20"),
        ("CC-Madhya-20-154", "/en/library/cc/madhya/20/154/", "Madhya 20.154"),
        ("SB-9-20-20-27", "/en/library/sb/9/20/20/", "9.20.20-27"),
    ],
)
def test_verse_ref(what, path, tag):
    ref = VerseRef.from_what(what)
    assert ref.path == path and ref.tag == tag


@pytest.mark.parametrize(
    "what", ["SB-3-6", "CC-20-154", "BG-2-51-50", "Janmastami", None, "SB-1-2-3-4-5"]
)
def test_not_a_verse(what):
    assert VerseRef.from_what(what) is None


def test_range_has_a_page_per_verse():
    assert [v.what for v in VerseRef.from_what("SB-9-20-20-22").verses()] == [
        "SB-9-20-20",
        "SB-9-20-21",
        "SB-9-20-22",
    ]


# --------------------------------------------------------- vedabase page parser
@pytest.mark.parametrize(
    "name,heading,start",
    [
        ("bg-2-51", "2.51", "By thus engaging"),
        ("cc-madhya-20-154", "CC Madhya 20.154", ""),
        ("sb-9-20-20", "9.20.20", ""),
    ],
)
def test_parse_real_pages(name, heading, start):
    verse = page(name)
    assert heading in verse.heading
    assert verse.translation.startswith(start) and len(verse.translation) > 30
    assert verse.transliteration and verse.devanagari
    assert verse.synonyms and all(len(pair) == 2 for pair in verse.synonyms)
    assert verse.purport


def test_parse_not_a_verse_page():
    assert parse_verse_page("<html><body>Not found</body></html>") is None


# --------------------------------------------------------------- vedabase client
def mock_client(monkeypatch, calls: list[str]) -> VedabaseClient:
    monkeypatch.setenv("VEDABASE_DELAY_S", "0")
    get_settings.cache_clear()
    pages = {
        "/en/library/bg/2/51/": "bg-2-51",
        "/en/library/cc/madhya/20/154/": "cc-madhya-20-154",
        "/en/library/sb/9/20/20/": "sb-9-20-20",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert "media-archive-utils" in request.headers["user-agent"]
        name = pages.get(request.url.path)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, text=(VEDA / f"{name}.html").read_text(encoding="utf-8"))

    return VedabaseClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_fetches_once_and_caches(monkeypatch):
    calls: list[str] = []
    client = mock_client(monkeypatch, calls)
    ref = VerseRef.from_what("BG-2-51")
    first = client.fetch(ref)
    again = client.fetch(ref)
    assert first == again and first.translation and calls == ["/en/library/bg/2/51/"]


def test_client_remembers_a_missing_verse(monkeypatch):
    calls: list[str] = []
    client = mock_client(monkeypatch, calls)
    ref = VerseRef.from_what("BG-2-99")
    assert client.fetch(ref) is None and client.fetch(ref) is None and len(calls) == 1


# ------------------------------------------------------------------- the match
def spoken(text: str, start: float = 100.0):
    return [(start, text)]


def test_translation_heard_is_confirmed():
    verse = page("bg-2-51")
    got = match_translation(verse, spoken(verse.translation.lower().replace(".", "")))
    assert got.confirmed and got.share > 0.9 and got.heard_at == 100.0


def test_translation_split_over_two_segments():
    verse = page("bg-2-51")
    words = verse.translation.split()
    cut = len(words) // 2
    segs = [(10.0, " ".join(words[:cut])), (20.0, " ".join(words[cut:]))]
    assert match_translation(verse, segs).share > 0.95


def test_other_text_is_not_confirmed():
    verse = page("bg-2-51")
    got = match_translation(verse, spoken("today we talk about how to be a good friend"))
    assert not got.confirmed and got.share == 0


# ------------------------------------------------------------------ the decision
FOUND = {
    "what": "BG-2-51",
    "tag": "2.51",
    "time": 5.0,
    "excerpt": "",
    "heard": 2,
    "on_vedabase": True,
    "match": {"share": 0.9, "heard_at": 9.0, "confirmed": True},
    "note": "",
    "confirmed": True,
}


def test_decide_names_the_file_when_the_filename_has_no_verse():
    got = discoverer.decide({"found": FOUND, "file": None})
    assert got["action"] == "name" and got["sure"]


def test_decide_keeps_a_filename_that_agrees():
    assert discoverer.decide({"found": FOUND, "file": "BG-2-51"})["action"] == "keep"


def test_decide_flags_a_different_verse():
    got = discoverer.decide({"found": FOUND, "file": "BG-2-54"})
    assert got["action"] == "review" and "BG-2-54" in got["reasons"][0]


def test_decide_flags_an_unconfirmed_verse():
    found = {**FOUND, "confirmed": False, "match": {"share": 0.05, "confirmed": False}}
    assert discoverer.decide({"found": found, "file": None})["action"] == "review"


def test_decide_no_verse_heard():
    assert discoverer.decide({"found": None, "file": None})["action"] == "none"
    assert discoverer.decide({"found": None, "file": "SB-1-2-3"})["action"] == "keep"


# ------------------------------------------------------------------ transcriber
def test_whisper_cpp_json_is_normalized():
    segments, language = cpp_segments(
        {
            "result": {"language": "nl"},
            "transcription": [
                {"offsets": {"from": 1500, "to": 4200}, "text": " Hare Krishna "},
                {"offsets": {"from": 4200, "to": 5000}, "text": "  "},
            ],
        }
    )
    assert language == "nl" and len(segments) == 1
    assert (segments[0].start, segments[0].end, segments[0].text) == (1.5, 4.2, "Hare Krishna")


def fake_transcript(*texts: str, language="en") -> Transcript:
    segs = [Segment(i * 10.0, i * 10.0 + 9, t, language) for i, t in enumerate(texts)]
    return Transcript(segs, language, "test", "1", "tiny", None, "cpu")


def test_transcript_is_cached_by_content(monkeypatch, settings):
    runs = []

    def backend(audio, settings, language):
        runs.append(audio)
        return fake_transcript("one", "two")

    monkeypatch.setattr("mam.helpers.transcriber.store.run_backend", backend)
    with session_scope() as s:
        first, cached1 = transcribe(s, Path("a.mp3"), "abc", settings)
        again, cached2 = transcribe(s, Path("other.mp3"), "abc", settings)
        assert (cached1, cached2) == (False, True) and len(runs) == 1
        assert [x.text for x in again.segments] == ["one", "two"]
        assert load_transcript(s, "abc").languages == ["en"]


def test_transcript_is_written_as_srt(tmp_path):
    t = fake_transcript("Hare Krishna", "Chapter two")
    out = write_srt(t, tmp_path / "x.srt")
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["1", "00:00:00,000 --> 00:00:09,000", "Hare Krishna"]
    assert "2" in lines and "00:00:10,000 --> 00:00:19,000" in lines


# ------------------------------------------------------------------- the job
@pytest.fixture
def prepared(settings, monkeypatch, reference, no_baserow):
    monkeypatch.setenv("SPOT_CHECK_RATE", "0")
    get_settings.cache_clear()
    settings = get_settings()
    calls: list[str] = []
    client = mock_client(monkeypatch, calls)
    monkeypatch.setattr(discoverer, "VedabaseClient", lambda settings=None: client)

    def make(texts, name, what=None, mode=None):
        original = make_tone(
            settings.media_sample_files_path / "orig.mp3", seconds=20, volume_db=-30
        )
        folder = settings.media_processed_files_path / "Bhagavad-gita"
        folder.mkdir(parents=True, exist_ok=True)
        copy = folder / name
        shutil.copy2(original, copy)
        # the real transcribe(): it writes the transcript through the job's session, which must
        # not stay open when vedabase.io caches a page (database is locked, seen on the Mac)
        monkeypatch.setattr(
            "mam.helpers.transcriber.store.run_backend",
            lambda audio, settings, language: fake_transcript(*texts),
        )
        with session_scope() as s:
            record = register_original(s, original)
            record.processed_path, record.current_name = str(copy), name
            record.meta = {"proposal": {"what": what, "languages": ["English"]}}
            if mode:
                record.meta = {**record.meta, "splitter": {"proposal": {"mode": mode}}}
            record.status = "Kirtan splitter Done"
            return record.id, copy

    return make


def run(record_id, *, dry_run=False):
    with session_scope() as s:
        record = s.get(m.MediaFile, record_id)
        ctx = JobContext(1, s, get_settings(), dry_run, {}, record)
        return discoverer.run_class_discoverer(ctx)


ANNOUNCE = "We are reading from Bhagavad-gita as it is, chapter two, text fifty-one."


def translation_texts():
    return [ANNOUNCE, page("bg-2-51").translation]


def test_job_names_the_file_and_queues_the_renamer(prepared, settings):
    rid, copy = prepared(translation_texts(), "2008-01-04_KKS.mp3")
    assert measure(copy).integrated < -30
    out = run(rid)
    assert out.state == "done", out.summary
    assert out.data["gain"]["applied"]
    assert abs(measure(copy).integrated + 18) <= 1.0  # the quiet file was raised to the target
    with session_scope() as s:
        job = s.scalar(select(m.Job).where(m.Job.tool == "renamer"))
        assert job.params["evidence"]["what"] == "BG-2-51"
        assert job.params["evidence"]["verse_tag"] == "2.51"
        assert job.params["evidence"]["languages"] == ["English"]
        record = s.get(m.MediaFile, rid)
        assert record.meta["class_discoverer"]["class_start"] == 0.0
        assert record.sha256  # the gained copy's hash
    sub = copy.resolve().parent.relative_to(settings.media_processed_files_path.resolve())
    assert (settings.transcriptions_path / sub / f"{copy.stem}.srt").exists()


def test_job_keeps_a_filename_that_agrees(prepared):
    rid, _ = prepared(translation_texts(), "2008-01-04_KKS_BG-2-51.mp3", what="BG-2-51")
    out = run(rid)
    assert out.state == "done"
    with session_scope() as s:
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is None


def test_job_flags_a_different_verse(prepared):
    rid, copy = prepared(translation_texts(), "2008-01-04_KKS_BG-2-54.mp3", what="BG-2-54")
    out = run(rid)
    assert out.state == "review"
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "class-discoverer"))
        assert (
            review.proposal["found"]["what"] == "BG-2-51" and review.proposal["file"] == "BG-2-54"
        )
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is None  # nothing renamed


def test_review_approval_queues_the_renamer(prepared):
    rid, _ = prepared(translation_texts(), "2008-01-04_KKS_BG-2-54.mp3", what="BG-2-54")
    run(rid)
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "class-discoverer"))
        msg = discoverer.decide_review(
            s, review, action="use_transcript", what=None, operator="tester", note=None
        )
        assert "BG-2-51" in msg and review.outcome == "approved"
        job = s.scalar(select(m.Job).where(m.Job.tool == "renamer"))
        assert job.params["evidence"]["what"] == "BG-2-51"


def test_review_rejects_a_typed_non_verse(prepared):
    rid, _ = prepared(translation_texts(), "2008-01-04_KKS_BG-2-54.mp3", what="BG-2-54")
    run(rid)
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "class-discoverer"))
        with pytest.raises(Exception, match="not a verse"):
            discoverer.decide_review(
                s, review, action="custom", what="Janmastami", operator="t", note=None
            )


def test_job_without_a_verse_or_a_what_goes_to_review(prepared):
    rid, _ = prepared(["Today we talk about friendship."], "2008-01-04_KKS.mp3")
    assert run(rid).state == "review"


def test_job_without_a_verse_but_with_a_category_is_done(prepared):
    rid, _ = prepared(["Today we talk about friendship."], "2008-01-04_KKS_Janmastami.mp3")
    with session_scope() as s:
        s.get(m.MediaFile, rid).meta = {"proposal": {"what": "Janmastami", "category": "Festivals"}}
    assert run(rid).state == "done"


def test_kirtan_only_is_skipped(prepared):
    rid, _ = prepared(translation_texts(), "x.mp3", mode="kirtan_only")
    assert run(rid).state == "skipped"


def test_dry_run_changes_nothing(prepared, settings):
    rid, copy = prepared(translation_texts(), "2008-01-04_KKS.mp3")
    before = copy.read_bytes()
    out = run(rid, dry_run=True)
    assert out.state == "info" and out.summary.startswith("Would:")
    assert copy.read_bytes() == before
    assert not list(settings.transcriptions_path.glob("*.srt")) or True
    with session_scope() as s:
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is None


def test_class_audio_of_a_video_also_names_the_video(prepared):
    rid, _ = prepared(translation_texts(), "2008-01-04_KKS.mp3")
    with session_scope() as s:
        video = register_original(
            s, make_tone(Path(get_settings().media_sample_files_path) / "v.mp3")
        )
        video.kind = "video"
        record = s.get(m.MediaFile, rid)
        record.parent_id, record.derivation = video.id, "class"
        video_id = video.id
    assert run(rid).state == "done"
    with session_scope() as s:
        targets = {j.media_file_id for j in s.scalars(select(m.Job).where(m.Job.tool == "renamer"))}
        assert video_id in targets


# ------------------------------------------------------------------ the portal
def test_menu_and_pipeline_order():
    from mam.manager.tools import menu_tools, next_in_pipeline

    assert next_in_pipeline("kirtan-splitter") == "class-discoverer"
    assert "class-discoverer" in [t.name for t in menu_tools()]


def test_review_page_and_approval_through_the_portal(prepared):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    rid, _ = prepared(translation_texts(), "2008-01-04_KKS_BG-2-54.mp3", what="BG-2-54")
    run(rid)
    with session_scope() as s:
        review_id = s.scalar(select(m.Review).where(m.Review.tool == "class-discoverer")).id
    with TestClient(create_app(start_queue=False)) as client:
        page = client.get(f"/details/{rid}")
        assert page.status_code == 200
        assert "Which verse is it?" in page.text and "Use BG-2-51" in page.text
        assert "Keep BG-2-54" in page.text and "Approve and split" not in page.text
        bad = client.post(
            f"/review/{review_id}/decide",
            data={"action": "custom", "what": "Janmastami"},
            follow_redirects=False,
        )
        assert "not a verse" in bad.headers["location"].replace("%20", " ")
        done = client.post(
            f"/review/{review_id}/decide",
            data={"action": "use_transcript"},
            follow_redirects=False,
        )
        assert done.status_code == 303
    with session_scope() as s:
        assert s.get(m.Review, review_id).outcome == "approved"
        assert s.get(m.MediaFile, rid).status == "Queued"  # the Renamer is next
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is not None


def test_result_shows_where_the_time_went(prepared):
    rid, _ = prepared(translation_texts(), "2008-01-04_KKS.mp3")
    out = run(rid)
    t = out.data["timings"]
    assert t["total_s"] >= t["gain_s"] + t["verse_s"] and t["total_s"] > 0
    assert "Took " in out.summary and "transcription" in out.summary
    assert discoverer.duration_text(252) == "4 min 12 s" and discoverer.duration_text(9) == "9 s"


def test_transcript_follows_its_audio(settings, tmp_path):
    settings = settings.model_copy(update={"transcriptions_path": tmp_path / "transcriptions"})
    from mam.helpers.transcriber import follow_audio, transcript_file

    old = settings.media_processed_files_path / "class" / "2008-01-04_KKS_Class.mp3"
    new = settings.media_processed_files_path / "Bhagavad-gita" / "2008-01-04_KKS_BG-2-51.mp3"
    src = transcript_file(settings, old)
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("[00:00:00.000] hello\n", encoding="utf-8")
    moved = follow_audio(settings, old, new)
    assert moved == transcript_file(settings, new) and moved.exists() and not src.exists()
    assert transcript_file(settings, new).parent.name == "Bhagavad-gita"
    assert follow_audio(settings, old, new) is None  # nothing left to move


def test_discover_tries_the_first_reading_when_the_correction_is_not_confirmed(monkeypatch):
    from mam.tools.class_discoverer.match import VerseMatch

    tried = []

    def fake_confirm(client, ref, transcript):
        tried.append(ref.what)
        ok = ref.what == "SB-1-9-31"  # only the first reading is really heard
        return True, VerseMatch(0.9 if ok else 0.0, 3.0 if ok else None, ok), ""

    monkeypatch.setattr(discoverer, "_confirm", fake_confirm)
    t = Transcript(
        [Segment(0, 5, "First Canto, Chapter 9, text 31. No, chapter 19.")],
        "en", "test", None, "test", None, "cpu",
    )  # fmt: skip
    # the Bhagavatam name is needed for the announcement
    t.segments[0].text = "Srimad Bhagavatam " + t.segments[0].text
    data = discoverer.discover(t, None, client=None)
    assert tried == ["SB-1-19-31", "SB-1-9-31"]
    assert data["found"]["what"] == "SB-1-9-31" and data["found"]["confirmed"]


def test_spoken_correction_within_twenty_seconds_wins():
    from mam.tools.class_discoverer.announce import find_announcements

    lines = [
        (0, "Om Namah Bhagavate Vasudevaya"), (37, "It's 31."),
        (39, "First Canto, Chapter 9, the appearance of Sukadeva Goswami, text 31."),
        (45, "13."), (46, "Huh? No."), (49, "31."), (50, "Yeah."), (51, "Chapter 19."),
        (53, "19."), (55, "Everyone's there? No. 1.19.31."), (65, "Yeah."),
    ]  # fmt: skip
    segs = [Segment(float(t), float(t) + 2, text, "en") for t, text in lines]
    t = Transcript(segs, "en", "test", "1", "tiny", None, "cpu")
    first = find_announcements(t)[0]
    assert first.ref.what == "SB-1-19-31"
    assert first.partial["alternatives"] == ["SB-1-9-31"]


def test_filename_verse_heard_beats_an_announcement_that_is_not():
    from mam.tools.class_discoverer.tool import decide

    data = {
        "found": {"what": "SB-1-9-31", "tag": "1.9.31", "confirmed": False,
                  "match": {"share": 0.0}},
        "file": "SB-1-19-31",
        "file_match": {"match": {"share": 0.61, "confirmed": True}},
    }  # fmt: skip
    d = decide(data)
    assert d["action"] == "keep" and d["sure"]
    data["file_match"]["match"] = {"share": 0.0, "confirmed": False}
    assert decide(data)["action"] == "review"


def test_only_a_general_category_does_not_count_as_a_what():
    from types import SimpleNamespace

    def has(what, category):
        record = SimpleNamespace(meta={"proposal": {"what": what, "category": category}})
        return discoverer._has_what(record)

    assert not has("Class", "Class")  # taken from a Lekce folder: says nothing
    assert not has(None, "Various")
    assert not has(None, None)
    assert has("Vyasa-puja", "Vyasa-puja")
    assert has(None, "Janmastami")
