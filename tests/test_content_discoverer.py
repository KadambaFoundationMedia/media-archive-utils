from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.llm import LLMError, LLMReply
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.app_settings import OPTION_BY_KEY, parse_option
from mam.helpers.local_db.repo import register_original
from mam.helpers.transcriber import Segment, Transcript
from mam.helpers.transcriber.store import save_transcript
from mam.helpers.vedabase import VerseRef
from mam.manager.tools import JobContext
from mam.tools.content_discoverer import prompt as P
from mam.tools.content_discoverer import tool as content
from mam.tools.content_discoverer.validate import check, youtube_description
from tests.conftest import make_tone

GOOD = {
    "topics": ["Krishna Prema", "Kali Yuga", "Humility"],
    "title": "Don't waste time",
    "description": "First paragraph about the class.\n\nSecond paragraph about humility.",
}


# ------------------------------------------------------------------ validation
def test_check_keeps_good_content():
    c = check(GOOD)
    assert c.topics == ["Krishna Prema", "Kali Yuga", "Humility"]
    assert c.title_words == "Dont-waste-time" and not c.problems


def test_topics_over_two_words_or_five_are_dropped():
    c = check({**GOOD, "topics": ["a b c", "One", "Two", "Three", "Four", "Five", "Six", "one"]})
    assert c.topics == ["One", "Two", "Three", "Four", "Five"]
    assert any("more than 2 words" in p for p in c.problems)
    assert any("first 5" in p for p in c.problems)


def test_long_title_is_cut_for_the_filename():
    c = check({**GOOD, "title": "A very long title that goes on and on forever and ever"})
    assert len(c.title_words) <= 48 and any("too long" in p for p in c.problems)


def test_description_gets_the_footer_with_the_year(tmp_path):
    footer = tmp_path / "f.txt"
    footer.write_text("Copyrights {year} Foundation\n", encoding="utf-8")
    out = youtube_description("Body.", footer, date(2031, 5, 1))
    assert out == "Body.\n\nCopyrights 2031 Foundation"


def test_repo_footer_matches_the_spec():
    text = youtube_description("x", get_settings().content_footer_path, date(2026, 1, 1))
    assert "https://kadambafoundation.com/donations" in text
    assert "Copyrights ℗ & © 2026 Kadamba Foundation" in text


def test_parse_reply_accepts_a_code_fence():
    assert P.parse_reply('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        P.parse_reply("no json here")


# ------------------------------------------------------------------ the filename
@pytest.mark.parametrize(
    "what,category,expected",
    [
        ("SB-9-20-20", "Srimad-bhagavatam", "SB-9-20-20-Dont-waste-time"),
        ("SB-9-20-20-27", "Srimad-bhagavatam", "SB-9-20-20-27-Dont-waste-time"),
        ("SB-9-20-20-Existing-title", "Srimad-bhagavatam", None),
        ("Vyasa-puja", "Vyasa-puja", None),  # no verse: the filename is left alone
        (None, None, None),
    ],
)
def test_plan_name(what, category, expected):
    new, _ = content.plan_name(what, category, "Dont-waste-time")
    assert new == expected


def test_plan_name_uses_the_verse_the_class_discoverer_found():
    new, _ = content.plan_name(None, None, "Dont-waste-time", VerseRef.from_what("BG-2-51"))
    assert new == "BG-2-51-Dont-waste-time"


def test_evidence_title_follows_the_renamer_rule():
    ev = content.evidence_for(
        "BG-2-51-Dont-waste-time", VerseRef.from_what("BG-2-51"), "Don't waste"
    )
    assert ev["what"] == "BG-2-51-Dont-waste-time" and ev["title"] == "BG 2.51 Dont waste"


# ------------------------------------------------------------------- the job
class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, user, *, max_tokens=4000):
        self.calls.append(user)
        return LLMReply(self.answers.pop(0), "fake", "fake-1", 1000, 200)


@pytest.fixture
def prepared(settings, monkeypatch, reference, no_baserow):
    monkeypatch.setenv("SPOT_CHECK_RATE", "0")
    monkeypatch.setenv("CONTENT_REVIEW_FIRST", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(content, "VedabaseClient", lambda settings=None: object())
    monkeypatch.setattr(content, "verse_context", lambda client, ref: None)

    def make(what="BG-2-51", category="Bhagavad-gita", answers=None):
        answers = answers or (json.dumps(GOOD),)
        provider = FakeProvider(answers)
        monkeypatch.setattr(content, "get_provider", lambda settings: provider)
        original = make_tone(get_settings().media_sample_files_path / "o.mp3", seconds=5)
        with session_scope() as s:
            record = register_original(s, original)
            record.sha256 = "abc123"
            record.meta = {
                "proposal": {"what": what, "category": category},
                "class_discoverer": {"class_start": 60.0, "languages": ["English"]},
            }
            record.status = "Class discoverer Done"
            rid = record.id
            save_transcript(
                s,
                Transcript(
                    [Segment(0, 9, "Greetings."), Segment(55, 70, "Time is precious.")],
                    "en",
                    "t",
                    "1",
                    "tiny",
                    None,
                    "cpu",
                    content_sha256="abc123",
                ),
            )
        return rid, provider

    return make


def run(rid, *, dry_run=False):
    with session_scope() as s:
        record = s.get(m.MediaFile, rid)
        return content.run_content_discoverer(JobContext(1, s, get_settings(), dry_run, {}, record))


def test_job_writes_results_and_queues_the_title(prepared):
    rid, provider = prepared()
    out = run(rid)
    assert out.state == "done", out.summary
    assert out.data["topics"][0] == "Krishna Prema"
    assert "Time is precious." in provider.calls[0] and "Greetings." not in provider.calls[0]
    with session_scope() as s:
        job = s.scalar(select(m.Job).where(m.Job.tool == "renamer"))
        assert job.params["evidence"]["what"] == "BG-2-51-Dont-waste-time"
        assert s.get(m.MediaFile, rid).meta["content_discoverer"]["title"] == "Don't waste time"


def test_second_run_uses_the_cache(prepared):
    rid, provider = prepared(answers=(json.dumps(GOOD),))
    run(rid)
    out = run(rid)
    assert out.data["usage"]["cached"] and len(provider.calls) == 1


def test_a_bad_answer_is_retried_once(prepared):
    rid, provider = prepared(answers=("not json", json.dumps(GOOD)))
    assert run(rid).state == "done" and len(provider.calls) == 2


def test_two_bad_answers_fail_the_job(prepared):
    rid, _ = prepared(answers=("nope", "still nope"))
    with pytest.raises(LLMError):
        run(rid)


def test_first_results_go_to_review(prepared, monkeypatch):
    monkeypatch.setenv("CONTENT_REVIEW_FIRST", "1")
    get_settings.cache_clear()
    rid, _ = prepared()
    out = run(rid)
    assert out.state == "review"
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "content-discoverer"))
        assert "Trial" in review.reason and review.proposal["new_what"].endswith("Dont-waste-time")
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is None  # nothing yet


def test_approval_edits_and_queues_the_renamer(prepared, monkeypatch):
    monkeypatch.setenv("CONTENT_REVIEW_FIRST", "1")
    get_settings.cache_clear()
    rid, _ = prepared()
    run(rid)
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "content-discoverer"))
        msg = content.decide_review(
            s,
            review,
            topics="Humility, Time",
            title="Time is precious",
            description="One.\n\nTwo.",
            use_new=set(),
            operator="t",
            note=None,
        )
        assert review.outcome == "edited" and "Renamer is queued" in msg
        job = s.scalar(select(m.Job).where(m.Job.tool == "renamer"))
        assert job.params["evidence"]["what"] == "BG-2-51-Time-is-precious"


def test_existing_baserow_values_make_a_review(prepared, monkeypatch):
    rid, _ = prepared()
    monkeypatch.setattr(
        content,
        "write_content",
        lambda *a, **k: {
            "action": "update",
            "row_id": 5,
            "dry_run": False,
            "conflicts": {"themes": ["Old", "New"]},
        },
    )
    out = run(rid)
    assert out.state == "review" and "themes" in out.summary
    with session_scope() as s:
        review = s.scalar(select(m.Review).where(m.Review.tool == "content-discoverer"))
        assert review.proposal["conflicts"]["themes"] == ["Old", "New"]


def test_dry_run_asks_the_model_but_changes_nothing(prepared):
    rid, provider = prepared()
    out = run(rid, dry_run=True)
    assert out.state == "info" and out.summary.startswith("Would:") and len(provider.calls) == 1
    with session_scope() as s:
        assert s.scalar(select(m.Job).where(m.Job.tool == "renamer")) is None
        assert "content_discoverer" not in s.get(m.MediaFile, rid).meta


def test_no_transcript_is_a_clear_error(prepared):
    rid, _ = prepared()
    with session_scope() as s:
        s.get(m.MediaFile, rid).sha256 = "other"
    with pytest.raises(Exception, match="Class Discoverer|class discoverer|Class discoverer"):
        run(rid)


def test_kirtan_only_is_skipped(prepared):
    rid, _ = prepared()
    with session_scope() as s:
        s.get(m.MediaFile, rid).derivation = "kirtan"
    assert run(rid).state == "skipped"


# ------------------------------------------------------------------- the portal
def test_settings_options_and_menu():
    from mam.manager.tools import menu_tools, next_in_pipeline

    assert next_in_pipeline("class-discoverer") == "content-discoverer"
    assert "content-discoverer" in [t.name for t in menu_tools()]
    assert parse_option(OPTION_BY_KEY["llm_effort"], "medium") == "medium"
    with pytest.raises(ValueError):
        parse_option(OPTION_BY_KEY["llm_effort"], "extreme")


def test_review_page_through_the_portal(prepared, monkeypatch):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app

    monkeypatch.setenv("CONTENT_REVIEW_FIRST", "1")
    get_settings.cache_clear()
    rid, _ = prepared()
    run(rid)
    with session_scope() as s:
        review_id = s.scalar(select(m.Review).where(m.Review.tool == "content-discoverer")).id
    with TestClient(create_app(start_queue=False)) as client:
        page = client.get(f"/details/{rid}")
        assert page.status_code == 200 and "Themes, title and description" in page.text
        assert "Krishna Prema, Kali Yuga" in page.text
        done = client.post(
            f"/review/{review_id}/decide",
            data={
                "action": "approve",
                "topics": "Time",
                "title": "Use time well",
                "description": "A.\n\nB.",
            },
            follow_redirects=False,
        )
        assert done.status_code == 303
        assert client.get("/settings").status_code == 200
    with session_scope() as s:
        assert s.get(m.Review, review_id).outcome == "edited"
        assert s.get(m.MediaFile, rid).status == "Content discoverer Done"


# ------------------------------------------------------- review remarks of 2026-11-24
def test_broad_themes_are_dropped():
    from mam.tools.content_discoverer.validate import broad_set

    broad = broad_set("Krishna Consciousness, Devotion")
    c = check({**GOOD, "topics": ["krishna-consciousness", "Humble Inquiry"]}, broad)
    assert c.topics == ["Humble Inquiry"]
    assert any("broad themes" in p for p in c.problems)


def test_description_must_name_place_and_year():
    c = check(GOOD, facts={"place": "Oslo", "year": "2011"})
    assert any("place (Oslo)" in p for p in c.problems)
    assert any("year (2011)" in p for p in c.problems)
    ok = {**GOOD, "description": "Recorded in Oslo in 2011.\n\nMore."}
    assert not check(ok, facts={"place": "Oslo", "year": "2011"}).problems


def test_custom_footer_wins_over_the_file(tmp_path):
    out = youtube_description(
        "Body.", get_settings().content_footer_path, date(2031, 1, 1), "(c) {year} X"
    )
    assert out == "Body.\n\n(c) 2031 X"


def test_facts_for_the_first_sentence():
    assert P.spoken_date("2011-08-20") == "20 August 2011"
    assert P.spoken_date("2011-MM-DD") is None
    assert P.place_text("Oslo", "no") == "Oslo, Norway"
    assert P.place_text("Zoom-livestream", None) == "Zoom livestream"


def test_glossary_spelling_in_the_answer():
    from mam.helpers.transcriber import glossary as G
    from mam.tools.content_discoverer.spelling import fix_content

    glossary = G.load(get_settings().sanskrit_glossary_path)
    out = fix_content(
        {"topics": ["Lord Chaitanya"], "title": "Chaitanya on humility", "description": "x"},
        glossary,
    )
    assert out["topics"] == ["Lord Caitanya"]
    assert out["title"].startswith("Caitanya")


def test_new_settings_are_options():
    assert OPTION_BY_KEY["content_footer"].kind == "textarea"
    assert parse_option(OPTION_BY_KEY["content_footer"], " a\r\nb ") == "a\nb"
    assert parse_option(OPTION_BY_KEY["content_broad_themes"], "A,  b ,") == "A, b"
