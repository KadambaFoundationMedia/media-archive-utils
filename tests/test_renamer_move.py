"""Renamer options: move instead of copy, and the postfix on the original a copy leaves behind."""

import errno
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from mam.errors import MamError
from mam.helpers.file_organizer import CategoryMatcher, FileOrganizer
from mam.helpers.file_organizer import organizer as org_module
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.helpers.local_db.app_settings import clean_postfix, set_renamer_options
from mam.helpers.local_db.hashing import sha256
from tests.test_manager import archive, client, ids, run_queue  # noqa: F401

NAME = "2011-08-20_KKS_SB-1-19-31_fi.mp3"


@pytest.fixture
def organizer(category_rows):
    return FileOrganizer(CategoryMatcher(category_rows))


def organize(organizer, src, **kw):
    return organizer.organize(src, NAME, "SB-1-19-31", tool="t", dry_run=False, **kw)


def test_move_takes_the_original_away_and_keeps_the_bytes(organizer, tone):
    before = sha256(tone)
    result = organize(organizer, tone, move_original=True)
    assert result.action == "move" and result.moved_original
    assert not tone.exists() and sha256(result.dst) == before


def test_move_dry_run_changes_nothing(organizer, tone):
    result = organizer.organize(tone, NAME, "SB-1-19-31", tool="t", move_original=True)
    assert result.moved_original and tone.exists() and not result.dst.exists()


def test_move_across_volumes_is_a_verified_copy(organizer, tone, monkeypatch):
    before = sha256(tone)

    def other_volume(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", other_volume)
    result = organize(organizer, tone, move_original=True)
    assert not tone.exists() and sha256(result.dst) == before
    assert not list(result.dst.parent.glob(".*.part"))


def test_a_bad_copy_never_removes_the_original(organizer, tone, monkeypatch):
    monkeypatch.setattr(os, "rename", lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
    monkeypatch.setattr(org_module, "_hash_file", lambda path: "not the same")
    with pytest.raises(MamError):
        organize(organizer, tone, move_original=True)
    assert tone.exists()
    target = organizer.root / "Srimad-bhagavatam"
    assert not (target / NAME).exists() and not list(target.glob(".*.part"))


def test_original_that_cannot_be_removed_stays_and_is_reported(organizer, tone, monkeypatch):
    monkeypatch.setattr(os, "rename", lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
    real_unlink = Path.unlink

    def refuse(self, *a, **kw):
        if self == tone:
            raise PermissionError("read-only share")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", refuse)
    result = organize(organizer, tone, move_original=True)
    assert tone.exists() and result.dst.exists()
    assert any("could not be removed" in w for w in result.warnings)


def test_a_move_never_overwrites(organizer, tone):
    first = organize(organizer, tone, move_original=False)
    # the same name with different content gets -2, so a move cannot replace the first file
    other = tone.with_name("other.mp3")
    other.write_bytes(b"different bytes")
    second = organize(organizer, other, move_original=True)
    assert first.dst.exists() and second.dst != first.dst and second.dst.exists()


def test_postfix_marks_the_original_a_copy_leaves_behind(organizer, tone):
    before = sha256(tone)
    result = organize(organizer, tone, postfix="_processed")
    marked = tone.with_name("tone_processed.mp3")
    assert result.marked_original == marked and marked.exists() and not tone.exists()
    assert sha256(marked) == sha256(result.dst) == before


def test_postfix_is_not_added_twice_and_never_overwrites(organizer, tone):
    done = tone.with_name("tone_processed.mp3")
    done.write_bytes(b"an earlier file")
    organize(organizer, tone, postfix="_processed")
    assert done.read_bytes() == b"an earlier file"
    assert tone.with_name("tone_processed-2.mp3").exists()
    again = organize(organizer, done, postfix="_processed")  # already marked
    assert again.marked_original is None and done.exists()


def test_postfix_dry_run_and_empty_postfix_change_nothing(organizer, tone):
    organizer.organize(tone, NAME, "SB-1-19-31", tool="t", postfix="_processed")
    assert tone.exists()
    organize(organizer, tone, postfix="")
    assert tone.exists()


def test_postfix_does_not_apply_to_a_move(organizer, tone):
    result = organize(organizer, tone, move_original=True, postfix="_processed")
    assert result.marked_original is None and not list(tone.parent.glob("*_processed*"))


def test_failed_rename_of_the_original_is_only_a_warning(organizer, tone, monkeypatch):
    real_rename = os.rename

    def refuse(src, dst):
        if Path(src) == tone:
            raise PermissionError("read-only share")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", refuse)
    result = organize(organizer, tone, postfix="_processed")
    assert result.dst.exists() and tone.exists() and result.warnings


@pytest.mark.parametrize("text", ["", "_processed", " -done ", "v2"])
def test_postfix_accepts_plain_text(text):
    assert clean_postfix(text) == text.strip()


@pytest.mark.parametrize("text", [".bak", "a/b", "x y", "é", "a" * 31])
def test_postfix_refuses_what_would_break_a_filename(text):
    with pytest.raises(ValueError):
        clean_postfix(text)


def test_settings_page_saves_the_renamer_block(client):
    page = client.get("/settings").text
    assert "Move files instead of copying" in page
    assert "Add postfix to processed filenames" in page
    client.post("/settings/renamer", data={"move_files": "on", "postfix": "_processed"})
    page = client.get("/settings").text
    assert 'value="_processed"' in page and "checked" in page.split("renamer_move_files")[1][:80]
    bad = client.post("/settings/renamer", data={"postfix": ".x"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]


CLEAN = "From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"
FLAGGED = "Serbia-summercamp-6-13-jul-2012/R09_0004.MP3"


def run_renamer_on(client, name):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)[Path(name).name]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    return file_id


def test_run_in_move_mode_and_reject_puts_the_file_back(
    client,
    archive,
    reference,
    no_baserow,
    settings,  # noqa: F811
):
    set_renamer_options(True, "_processed")
    file_id = run_renamer_on(client, FLAGGED)
    original = archive / FLAGGED
    copy = settings.media_processed_files_path / "_unresolved" / "2012-07-DD_KKS_rs.mp3"
    assert copy.exists() and not original.exists()
    assert not list(original.parent.glob("*_processed*"))  # no postfix on a move
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
        assert s.get(m.MediaFile, file_id).meta["original_moved"]
    before = sha256(copy)

    client.post(f"/review/{review_id}/decide", data={"action": "reject", "note": "wrong"})
    assert original.exists() and not copy.exists() and sha256(original) == before
    with session_scope() as s:
        record = s.get(m.MediaFile, file_id)
        assert record.status == "Not processed" and "original_moved" not in record.meta


def test_exclude_in_move_mode_never_trashes_the_only_copy(
    client,
    archive,
    reference,
    no_baserow,
    settings,
    monkeypatch,  # noqa: F811
):
    set_renamer_options(True, "")
    file_id = run_renamer_on(client, FLAGGED)
    trashed = []
    monkeypatch.setattr("mam.helpers.file_organizer.trash._send_to_trash", trashed.append)
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
    client.post(f"/review/{review_id}/decide", data={"action": "exclude", "note": "no"})
    assert trashed == [] and (archive / FLAGGED).exists()
    with session_scope() as s:
        assert s.get(m.MediaFile, file_id).status == "Excluded"


def test_reset_refuses_to_delete_the_only_copy(
    client,
    archive,
    reference,
    no_baserow,
    settings,  # noqa: F811
):
    from mam.manager.maintenance import ResetOptions, reset

    set_renamer_options(True, "")
    run_renamer_on(client, CLEAN)
    with pytest.raises(MamError, match="only one"):
        reset(ResetOptions(processed_files=True))
    assert any(settings.media_processed_files_path.rglob("*.mp3"))


def test_run_in_copy_mode_marks_the_original_and_reject_restores_its_name(
    client,
    archive,
    reference,
    no_baserow,
    settings,  # noqa: F811
):
    set_renamer_options(False, "_processed")
    file_id = run_renamer_on(client, FLAGGED)
    original = archive / FLAGGED
    marked = original.with_name("R09_0004_processed.MP3")
    assert marked.exists() and not original.exists()
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
    client.post(f"/review/{review_id}/decide", data={"action": "reject", "note": "wrong"})
    assert original.exists() and not marked.exists()
    with session_scope() as s:
        assert s.get(m.MediaFile, file_id).status == "Not processed"


def test_rerun_after_marking_still_finds_the_file(
    client,
    archive,
    reference,
    no_baserow,
    settings,  # noqa: F811
):
    set_renamer_options(False, "_processed")
    file_id = run_renamer_on(client, CLEAN)
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    with session_scope() as s:
        record = s.get(m.MediaFile, file_id)
        assert record.status in ("Renamer Done", "Renamer Review")
    processed = list(settings.media_processed_files_path.rglob("*.mp3"))
    assert len(processed) == 1  # a re-run moves the copy, it never makes a second one
