"""Worker pools scale with the computer's load."""

import pytest
from fastapi.testclient import TestClient

from mam.manager.app import create_app
from mam.manager.resources import next_limit


@pytest.fixture
def client(settings):
    with TestClient(create_app(start_queue=False)) as c:
        yield c


def test_adds_a_worker_when_there_is_room_and_work_waits():
    assert next_limit(2, 8, cpu=30, memory_free=50, waiting=True) == 3


def test_does_not_add_without_waiting_jobs_or_at_the_maximum():
    assert next_limit(2, 8, cpu=30, memory_free=50, waiting=False) == 2
    assert next_limit(8, 8, cpu=30, memory_free=50, waiting=True) == 8


def test_takes_a_worker_away_when_busy_or_low_on_memory_but_keeps_one():
    assert next_limit(4, 8, cpu=95, memory_free=50, waiting=True) == 3
    assert next_limit(4, 8, cpu=30, memory_free=5, waiting=True) == 3
    assert next_limit(1, 8, cpu=99, memory_free=2, waiting=True) == 1


def test_holds_between_the_two_thresholds():
    assert next_limit(3, 8, cpu=75, memory_free=50, waiting=True) == 3


def test_bar_colors():
    from mam.manager.resources import level

    assert [level("cpu", v) for v in (10, 70, 90)] == ["success", "warning", "danger"]
    assert [level("temp", v) for v in (60, 75, 90)] == ["success", "warning", "danger"]


def test_header_bars_show_what_is_available(client, monkeypatch):
    monkeypatch.setattr(
        "mam.manager.resources.snapshot",
        lambda: {"cpu": 12.0, "memory": 70.0, "memory_free": 30.0, "gpu": None, "temp": None},
    )
    html = client.get("/nav/resources").text
    assert "CPU" in html and "Memory" in html and "GPU" not in html and "temperature" not in html
    assert "bg-success" in html and "bg-warning" in html
