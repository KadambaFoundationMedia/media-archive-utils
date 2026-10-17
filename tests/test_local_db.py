import shutil

from sqlalchemy import inspect, select

from mam.helpers.local_db import get_engine, session_scope
from mam.helpers.local_db import models as m
from mam.helpers.local_db.repo import (
    export_db,
    import_db,
    register_derived,
    register_original,
    set_status,
)


def test_migration_creates_all_tables():
    tables = set(inspect(get_engine()).get_table_names())
    assert set(m.Base.metadata.tables) <= tables


def test_register_original_links_identical_copies(tone, settings):
    copy = settings.media_sample_files_path / "sub" / "same.mp3"
    copy.parent.mkdir()
    shutil.copy2(tone, copy)
    with session_scope() as s:
        first = register_original(s, tone)
        second = register_original(s, copy)
        assert first.id == second.id
        assert [a.path for a in first.aliases] == [str(copy.resolve())]
        assert first.sha256  # computed to confirm the duplicate
        assert register_original(s, tone).id == first.id  # idempotent


def test_non_media_files_are_ignored(settings):
    doc = settings.media_sample_files_path / "notes.pdf"
    doc.write_bytes(b"%PDF")
    with session_scope() as s:
        assert register_original(s, doc) is None


def test_status_history_and_lineage(tone, settings):
    derived = settings.media_processed_files_path / "class.mp3"
    shutil.copy2(tone, derived)
    with session_scope() as s:
        parent = register_original(s, tone)
        set_status(s, parent, "Queued renamer", tool="renamer")
        set_status(s, parent, "Processing renamer", tool="renamer")
        child = register_derived(s, parent, derived, "class")
        assert child.parent_id == parent.id
        history = s.scalars(select(m.StatusHistory).order_by(m.StatusHistory.id)).all()
        assert [h.new_status for h in history] == ["Queued renamer", "Processing renamer"]


def test_export_and_import_roundtrip(tone, settings, tmp_path):
    with session_scope() as s:
        register_original(s, tone)
    export = export_db(get_engine(), tmp_path / "exports")
    assert (export / "mam.sqlite").exists()
    assert "tone.mp3" in (export / "media_file.json").read_text()

    other = tmp_path / "other.sqlite"
    other.write_bytes(b"old")
    backup = import_db(export, other)
    assert backup is not None and backup.read_bytes() == b"old"
    assert other.read_bytes()[:15] == b"SQLite format 3"
