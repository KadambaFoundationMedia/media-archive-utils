"""Editing a file on its page, and how later Renamer runs treat confirmed values."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.manager.app import create_app
from mam.manager.queue import enqueue
from tests.test_manager import approve_form, archive, ids, run_queue  # noqa: F401


@pytest.fixture
def client(settings):
    with TestClient(create_app(start_queue=False)) as c:
        yield c


@pytest.fixture
def imported(client, archive, reference, no_baserow):  # noqa: F811
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        return ids(s)


def save_form(filename, **extra):
    data = approve_form(filename, **extra)
    data.pop("action")
    return data


def test_file_page_has_the_form_filled_in(client, imported):
    file_id = imported["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "mode": "preview"})
    run_queue()
    page = client.get(f"/files/{file_id}").text
    assert 'name="filename" value="2011-08-28_KKS_SB-1-19-30_Oslo-no.mp3"' in page
    assert 'name="tag" value="1.19.30"' in page
    assert '<option value="Srimad-bhagavatam" selected>' in page
    assert ">Save</button>" in page or "Save</button>" in page


def test_saving_renames_writes_baserow_and_confirms(client, imported, no_baserow, settings):
    file_id = imported["R09_0004.MP3"]
    response = client.post(
        f"/files/{file_id}/edit",
        data=save_form("2012-07-12_KKS_Kirtan_Fruska-Gora-rs.mp3", place="Fruska-Gora"),
        follow_redirects=False,
    )
    assert "Saved 2012-07-12_KKS_Kirtan_Fruska-Gora-rs.mp3" in response.headers["location"].replace(
        "%20", " "
    )
    copy = (
        settings.media_processed_files_path / "Kirtan" / "2012-07-12_KKS_Kirtan_Fruska-Gora-rs.mp3"
    )
    assert copy.exists()
    assert no_baserow[-1]["live"] is True  # a person's save always writes
    assert no_baserow[-1]["values"]["place_name"] == "Fruska-Gora"
    with session_scope() as s:
        rec = s.get(m.MediaFile, file_id)
        assert rec.status == "Renamer Done" and rec.current_name == copy.name
        assert rec.meta["confirmed"]["by"] == "tester"


def test_saving_closes_an_open_review(client, imported, no_baserow):
    file_id = imported["R09_0004.MP3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "mode": "run"})
    run_queue()
    client.post(f"/files/{file_id}/edit", data=save_form("2012-07-12_KKS_Kirtan_rs.mp3"))
    with session_scope() as s:
        review = s.scalars(select(m.Review)).one()
        assert review.outcome == "edited" and review.operator == "tester"


def test_save_refuses_a_broken_filename(client, imported, settings):
    file_id = imported["R09_0004.MP3"]
    response = client.post(
        f"/files/{file_id}/edit",
        data={**save_form("2012-07-12_KKS_Kirtan_rs.mp3"), "filename": "12-07-2012 kirtan.mp3"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert not any(settings.media_processed_files_path.rglob("*.mp3"))


def rerun(file_id, **params):
    with session_scope() as s:
        enqueue(s, "renamer", [file_id], dry_run=False, params=params or None)
    run_queue()
    with session_scope() as s:
        rec = s.get(m.MediaFile, file_id)
        job = s.scalars(
            select(m.Job).where(m.Job.media_file_id == file_id).order_by(m.Job.id.desc())
        ).first()
        return rec, job


def test_rerun_keeps_what_a_person_saved(client, imported):
    file_id = imported["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"]
    # the person decides the country is Finland, against the Renamer's guess (Norway)
    client.post(f"/files/{file_id}/edit", data=save_form("2011-08-28_KKS_SB-1-19-30_Oslo-fi.mp3"))
    rec, job = rerun(file_id)
    assert rec.current_name == "2011-08-28_KKS_SB-1-19-30_Oslo-fi.mp3"
    assert "no new information" in job.result["summary"]
    assert rec.status == "Renamer Done"


def test_another_tool_fills_in_a_missing_what(client, imported):
    file_id = imported["R09_0004.MP3"]
    client.post(f"/files/{file_id}/edit", data=save_form("2012-07-12_KKS_rs.mp3", category=""))
    rec, job = rerun(file_id, evidence={"what": "BG-2-51", "source": "class-discoverer"})
    assert rec.current_name == "2012-07-12_KKS_BG-2-51_rs.mp3"
    assert rec.category == "Bhagavad-gita"
    assert rec.meta["proposal"]["verse_tag"] == "2.51"
    assert any("Filled in from new evidence: what" in n for n in rec.meta["proposal"]["notes"])


def test_another_tool_completes_a_date_with_placeholders(client, imported):
    file_id = imported["R09_0004.MP3"]
    client.post(f"/files/{file_id}/edit", data=save_form("2012-07-DD_KKS_Kirtan_rs.mp3"))
    rec, _ = rerun(file_id, evidence={"when": "2012-07-12", "source": "class-discoverer"})
    assert rec.current_name == "2012-07-12_KKS_Kirtan_rs.mp3"


def test_conflicting_tool_evidence_is_flagged_not_overwritten(client, imported):
    file_id = imported["R09_0004.MP3"]
    client.post(f"/files/{file_id}/edit", data=save_form("2012-07-12_KKS_Kirtan_rs.mp3"))
    rec, job = rerun(file_id, evidence={"what": "BG-2-51", "source": "class-discoverer"})
    assert rec.current_name == "2012-07-12_KKS_Kirtan_rs.mp3"  # the person's WHAT stays
    assert rec.status == "Renamer Review"
    assert any(
        "class-discoverer found what 'BG-2-51', but tester set it to 'Kirtan'" in f
        for f in job.result["flags"]
    )


def test_notes_are_kept_and_sent_to_baserow(client, imported, no_baserow, reference, settings):
    from pathlib import Path

    from mam.tools.renamer.tool import baserow_values

    file_id = imported["R09_0004.MP3"]
    note = "Edirol recorder; recorded in our house in Amsterdam"
    client.post(
        f"/files/{file_id}/edit",
        data={**save_form("2012-07-12_KKS_Kirtan_rs.mp3"), "notes": note},
    )
    sent = no_baserow[-1]["values"]
    assert sent["notes_text"] == note and sent["notes_by"] == "tester"
    values = baserow_values(sent, Path(sent["target"]), "audio", reference, settings)
    assert values["notes"].endswith(f"Note (tester): {note}")
    page = client.get(f"/files/{file_id}").text
    assert note in page  # shown on the file page and kept in the form
    assert f">{note}</textarea>" in page


def test_a_never_previewed_file_starts_with_a_proposed_name(client, imported):
    """Without a preview the form must not start with the original name (it would never save)."""
    page = client.get(f"/files/{imported['R09_0004.MP3']}").text
    assert 'name="filename" value="2012-07-DD_KKS_rs.mp3"' in page
    assert 'id="edit-problems" class="small text-danger mt-3 mb-0"></ul>' in page  # no problems


def test_the_form_is_not_replaced_while_syncing(client, imported):
    """The bug: the sync replaced the form (and its Save button) under the user's click."""
    page = client.get(f"/files/{imported['R09_0004.MP3']}").text
    assert 'data-sync="/files/' in page and "hx-swap" not in page.split('id="edit-form"')[1][:400]
    response = client.post(
        f"/files/{imported['R09_0004.MP3']}/edit/sync",
        data={"filename": "2012-07-12_KKS_Kirtan_rs.mp3", "changed": "filename"},
    )
    assert response.headers["content-type"].startswith("application/json")


def test_save_then_page_shows_the_saved_values(client, imported, settings):
    """Save works the first time and the page shows what was saved."""
    file_id = imported["R09_0004.MP3"]
    note = "Home program at Uddhava/Visakha"
    response = client.post(
        f"/files/{file_id}/edit",
        data={**save_form("2008-MM-DD_KKS_Home-program_rs.mp3"), "notes": note},
        follow_redirects=False,
    )
    assert "Saved 2008-MM-DD_KKS_Home-program_rs.mp3" in response.headers["location"].replace(
        "%20", " "
    )
    page = client.get(f"/files/{file_id}").text
    assert 'name="filename" value="2008-MM-DD_KKS_Home-program_rs.mp3"' in page
    assert f">{note}</textarea>" in page
    copy = (
        settings.media_processed_files_path / "Home-program" / "2008-MM-DD_KKS_Home-program_rs.mp3"
    )
    assert copy.exists()
    # change the filename and save again: the copy is renamed, not duplicated
    client.post(
        f"/files/{file_id}/edit",
        data={**save_form("2008-MM-DD_KKS_Home-program-Uddhava_rs.mp3"), "notes": note},
    )
    renamed = copy.with_name("2008-MM-DD_KKS_Home-program-Uddhava_rs.mp3")
    assert renamed.exists() and not copy.exists()
    assert (
        'value="2008-MM-DD_KKS_Home-program-Uddhava_rs.mp3"' in client.get(f"/files/{file_id}").text
    )


def test_resaving_updates_the_linked_row_with_the_persons_values(
    client,
    archive,
    reference,
    settings,
    monkeypatch,  # noqa: F811
):
    """A person's save replaces the row's values and the file location; tools only fill gaps."""
    calls = []

    class Client:
        def __init__(self, *a, **k):
            self.media_table = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def create_row(self, table, values, **kw):
            calls.append(("create", values, kw))
            from mam.helpers.baserow.client import WriteResult

            return WriteResult(1, False, 777, values)

        def update_row(self, table, row_id, values, **kw):
            calls.append(("update", values, kw))
            from mam.helpers.baserow.client import WriteResult

            return WriteResult(2, False, row_id, values)

    monkeypatch.setattr("mam.helpers.baserow.BaserowClient", Client)
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    first = client.post(
        f"/files/{file_id}/edit",
        data=save_form("2012-07-12_KKS_Kirtan_rs.mp3"),
        follow_redirects=False,
    )
    assert "error" not in first.headers["location"], first.headers["location"]
    client.post(
        f"/files/{file_id}/edit",
        data={
            **save_form("2012-07-12_KKS_Kirtan_Prague-cz.mp3", place="Prague"),
            "baserow_row_id": "777",
        },
    )
    action, values, kw = calls[-1]
    assert action == "update"
    assert values["audio_file_location"] == "/Kirtan/2012-07-12_KKS_Kirtan_Prague-cz.mp3"
    assert {"audio_file_location", "title", "place_location", "country"} <= set(kw["overwrite"])
