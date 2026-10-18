"""Baserow reference data (places, categories and their colors) is served from cache."""

import json
import os
import threading
import time

import pytest

from mam.tools.renamer import knowledge
from tests.renamer_fixtures import reference_raw


@pytest.fixture
def disk_cache(settings, monkeypatch):
    monkeypatch.setattr(knowledge, "_cached", None)
    monkeypatch.setattr(knowledge, "_preloading", threading.Event())  # no refresh from other tests
    path = knowledge._cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reference_raw()), encoding="utf-8")
    return path


@pytest.fixture
def slow_baserow(monkeypatch):
    """Baserow that takes 'forever'; records calls instead of answering."""
    calls, release = [], threading.Event()

    def fetch():
        calls.append(time.time())
        release.wait(5)
        raise RuntimeError("no Baserow in tests")

    monkeypatch.setattr(knowledge, "fetch_raw", fetch)
    yield calls
    release.set()


def test_colors_come_from_the_disk_cache_without_waiting(disk_cache, slow_baserow):
    started = time.time()
    ref = knowledge.reference_if_ready()
    assert time.time() - started < 1  # did not wait for Baserow
    assert ref.matcher.by_name("Bhagavad-gita").color == "blue"


def test_stale_data_is_served_while_refreshing_in_the_background(disk_cache, slow_baserow):
    old = time.time() - 3600
    os.utime(disk_cache, (old, old))  # an hour old: due for a refresh
    started = time.time()
    ref = knowledge.get_reference()
    assert time.time() - started < 1 and ref is not None
    time.sleep(0.2)
    assert len(slow_baserow) == 1  # one background refresh started


def test_file_list_never_waits_for_baserow(settings, disk_cache, slow_baserow, tone):
    from fastapi.testclient import TestClient

    from mam.manager.app import create_app
    from tests.test_manager import run_queue

    with TestClient(create_app(start_queue=False)) as client:
        client.post("/import", data={"root": 0, "path": ""})
        run_queue()
        started = time.time()
        page = client.get("/", params={"status": "__all"})
        assert page.status_code == 200 and time.time() - started < 2
