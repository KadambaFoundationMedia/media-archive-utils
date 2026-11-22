"""Reset from the Settings page: clean start, originals and Baserow untouched."""

import time
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.manager.app import create_app
from mam.manager.maintenance import ResetOptions, reset
from tests.test_manager import archive, ids, run_queue  # noqa: F401  (fixtures)


@pytest.fixture
def client(settings):
    with TestClient(create_app(start_queue=False)) as c:
        yield c


def count(model):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model))


@pytest.fixture
def worked(client, archive, reference, no_baserow, settings):  # noqa: F811
    """An archive that was imported, renamed for real and partly reviewed."""
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    client.post("/actions", data={"tool": "renamer", "scope": "all", "mode": "run"})
    run_queue()
    with session_scope() as s:
        s.add(m.AliasRule(field="where", token="rdesh", replacement="Radhadesh", occurrences=3))
    return archive


def test_settings_page_shows_what_a_reset_removes(client, worked):
    page = client.get("/settings").text
    assert "Reset the media manager" in page
    assert "<strong>2</strong> file records" in page
    assert "Originals (sample files, trimming samples, inbox, NAS) are never touched." in page


def test_reset_needs_the_word_reset(client, worked):
    response = client.post("/settings/reset", data={"confirm": "yes"}, follow_redirects=False)
    assert "Type RESET" in unquote(response.headers["location"])
    assert count(m.MediaFile) == 2


def test_reset_clears_records_and_copies_but_not_originals(client, worked, settings):
    originals = sorted(p.name for p in worked.rglob("*") if p.is_file())
    assert any(settings.media_processed_files_path.rglob("*.mp3"))
    (settings.media_processed_files_path / ".gitkeep").write_text("")
    transcript = settings.transcriptions_path / "class" / "x.srt"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("[00:00:00.000] hello\n")

    response = client.post(
        "/settings/reset",
        data={"confirm": "RESET", "processed_files": "on"},
        follow_redirects=False,
    )
    assert "Reset done" in unquote(response.headers["location"])
    for model in (m.MediaFile, m.Job, m.Review, m.WEvidence, m.FileEvent, m.StatusHistory):
        assert count(model) == 0
    assert count(m.AliasRule) == 1  # learned corrections kept by default
    assert not any(
        p for p in settings.media_processed_files_path.rglob("*") if p.name != ".gitkeep"
    )
    assert (settings.media_processed_files_path / ".gitkeep").exists()
    assert not transcript.exists() and not transcript.parent.exists()  # transcripts go too
    assert sorted(p.name for p in worked.rglob("*") if p.is_file()) == originals
    backups = list((settings.local_db_path.parent / "backups").glob("mam-export-*/mam.sqlite"))
    assert len(backups) == 1

    # A fresh import works again after the reset.
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    assert count(m.MediaFile) == 2


def test_reset_can_forget_learned_corrections(worked, settings):
    reset(ResetOptions(processed_files=False, learned=True))
    assert count(m.AliasRule) == 0
    assert any(settings.media_processed_files_path.rglob("*.mp3"))  # copies kept on request


def test_reset_refuses_while_jobs_run(worked):
    with session_scope() as s:
        s.add(m.Job(tool="renamer", pool="io", state="running"))
    with pytest.raises(MamError, match="queued or running"):
        reset(ResetOptions())
    assert count(m.MediaFile) == 2


def test_reset_refuses_when_processed_folder_overlaps_originals(worked, settings, monkeypatch):
    monkeypatch.setattr(settings, "media_processed_files_path", settings.media_sample_files_path)
    with pytest.raises(MamError, match="overlaps an originals folder"):
        reset(ResetOptions())
    assert count(m.MediaFile) == 2


def test_reset_can_delete_earlier_backups_but_keeps_its_own(worked, settings):
    backup_dir = settings.local_db_path.parent / "backups"
    for name in ("mam-export-20200101-000000", "mam-export-20200102-000000"):
        (backup_dir / name).mkdir(parents=True)
        (backup_dir / name / "mam.sqlite").write_text("x")
    (backup_dir / "mine.txt").write_text("keep")
    kept = reset(ResetOptions(processed_files=False))
    assert kept.backups_deleted == 0
    time.sleep(1.1)  # backup folder names have a one-second resolution
    assert (backup_dir / "mam-export-20200101-000000").exists()
    result = reset(ResetOptions(processed_files=False, old_backups=True))
    assert result.backups_deleted == 3  # the two old ones and the first reset's own backup
    assert not (backup_dir / "mam-export-20200101-000000").exists()
    from pathlib import Path

    assert Path(result.backup).is_dir()
    assert (backup_dir / "mine.txt").exists()
