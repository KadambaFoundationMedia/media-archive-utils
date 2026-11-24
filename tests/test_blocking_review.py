from __future__ import annotations

from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.repo import register_original
from mam.manager.queue import blocking_review, enqueue
from tests.conftest import make_tone


def _file_with_review(tool: str):
    path = make_tone(get_settings().media_sample_files_path / "r.mp3", seconds=3)
    with session_scope() as s:
        record = register_original(s, path)
        s.add(m.Review(media_file_id=record.id, tool=tool, reason="check", proposal={}))
        s.flush()
        return record.id


def test_later_tools_wait_for_an_open_review_of_an_earlier_tool(settings):
    rid = _file_with_review("kirtan-splitter")
    with session_scope() as s:
        assert blocking_review(s, rid, "kirtan-splitter") is None  # not itself
        assert blocking_review(s, rid, "renamer") is None  # not an earlier tool's concern
        for tool in ("class-discoverer", "class-loudness", "content-discoverer"):
            assert blocking_review(s, rid, tool) == "Kirtan splitter"


def test_enqueue_skips_a_file_that_waits_and_says_why(settings):
    rid = _file_with_review("kirtan-splitter")
    blocked: list[tuple[int, str]] = []
    with session_scope() as s:
        assert enqueue(s, "content-discoverer", [rid], dry_run=False, blocked=blocked) == 0
        assert blocked == [(rid, "Kirtan splitter")]
        assert not s.scalars(select(m.Job)).all()
        # a dry run changes nothing, so it may run
        assert enqueue(s, "content-discoverer", [rid], dry_run=True) == 1


def test_a_decided_review_no_longer_blocks(settings):
    rid = _file_with_review("kirtan-splitter")
    with session_scope() as s:
        for review in s.scalars(select(m.Review)):
            review.outcome = "approved"
    with session_scope() as s:
        assert blocking_review(s, rid, "content-discoverer") is None
