from __future__ import annotations

import threading
import time

from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.repo import register_original
from mam.manager.queue import enqueue
from mam.manager.tools import TOOLS, JobContext, menu_tools
from mam.tools.class_discoverer import gain as G
from tests.conftest import make_tone


def _record(settings, name="a.mp3"):
    path = make_tone(get_settings().media_sample_files_path / name, seconds=3)
    with session_scope() as s:
        record = register_original(s, path)
        record.processed_path = str(path)
        s.flush()
        return record.id, path


def test_class_discoverer_queues_the_volume_step_on_the_cpu_pool(settings):
    rid, _ = _record(settings)
    with session_scope() as s:
        assert enqueue(s, "class-discoverer", [rid], dry_run=True) == 1
        jobs = s.scalars(select(m.Job)).all()
        assert [j.tool for j in jobs] == ["class-discoverer"]  # a dry run only measures
    with session_scope() as s:
        for job in s.scalars(select(m.Job)):
            s.delete(job)
    with session_scope() as s:
        enqueue(s, "class-discoverer", [rid], dry_run=False)
        enqueue(s, "class-discoverer", [rid], dry_run=False)  # no second copy
    with session_scope() as s:
        jobs = {j.tool: j.pool for j in s.scalars(select(m.Job))}
    assert jobs == {"class-discoverer": "ml", "audio-gain": "cpu"}


def test_the_volume_step_is_not_in_the_action_menu():
    assert "audio-gain" in TOOLS
    assert "audio-gain" not in {t.name for t in menu_tools()}


def test_the_volume_step_keeps_what_it_did_for_the_class_discoverer(settings, monkeypatch):
    rid, _ = _record(settings)
    monkeypatch.setattr(
        G, "apply_gain", lambda *a, **k: {"applied": True, "gain_db": 3.0, "mode": "linear"}
    )
    with session_scope() as s:
        record = s.get(m.MediaFile, rid)
        outcome = G.run_audio_gain(JobContext(1, s, get_settings(), False, {}, record))
        meta = dict(record.meta)
    assert outcome.state == "info" and outcome.data["gain"]["gain_db"] == 3.0
    assert meta["gain_pre"]["applied"] is True


def test_the_volume_step_skips_a_kirtan(settings):
    rid, _ = _record(settings)
    with session_scope() as s:
        record = s.get(m.MediaFile, rid)
        record.derivation = "kirtan"
        outcome = G.run_audio_gain(JobContext(1, s, get_settings(), False, {}, record))
    assert outcome.state == "skipped"


def test_one_gain_at_a_time_per_file(tmp_path):
    audio = tmp_path / "x.mp3"
    audio.write_bytes(b"x")
    order: list[str] = []

    def work(tag):
        with G.file_lock(audio):
            order.append(f"{tag}-in")
            time.sleep(0.05)
            order.append(f"{tag}-out")

    threads = [threading.Thread(target=work, args=(t,)) for t in "ab"]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])
    with G.file_lock(audio), G.file_lock(audio):  # re-entrant
        pass
