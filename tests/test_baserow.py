from datetime import date

import httpx
import pytest
import respx
from sqlalchemy import select

from mam.errors import BaserowError
from mam.helpers.baserow import BaserowClient, UnknownOption, merge_update
from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope

API = "https://baserow.test/api/database"
FIELDS = [
    {"name": "title", "type": "text"},
    {"name": "notes", "type": "long_text"},
    {"name": "date", "type": "date"},
    {"name": "Last modified", "type": "last_modified"},
    {
        "name": "country",
        "type": "single_select",
        "select_options": [{"id": 10, "value": "Norway"}, {"id": 11, "value": "Sweden"}],
    },
    {
        "name": "language",
        "type": "multiple_select",
        "select_options": [{"id": 20, "value": "English"}, {"id": 21, "value": "Czech"}],
    },
]
ROW = {
    "id": 5,
    "title": "Existing title",
    "notes": "",
    "date": None,
    "country": None,
    "language": [{"id": 20, "value": "English", "color": "blue"}],
}


# ------------------------------------------------------------------ merge policy
def test_merge_fills_empty_and_reports_conflicts():
    result = merge_update(
        ROW,
        {"title": "New title", "date": "2011-08-29", "country": "Norway"},
        multi_select_fields={"language"},
        tool="renamer",
    )
    assert result.payload == {"date": "2011-08-29", "country": "Norway"}
    assert result.conflicts == {"title": ("Existing title", "New title")}


def test_merge_appends_notes_and_unions_languages():
    current = {**ROW, "notes": "[2026-01-01 renamer] first"}
    result = merge_update(
        current,
        {"notes": "second", "language": ["English", "Czech"]},
        multi_select_fields={"language"},
        tool="class-discoverer",
        today=date(2026, 10, 17),
    )
    assert result.payload["notes"] == (
        "[2026-01-01 renamer] first\n[2026-10-17 class-discoverer] second"
    )
    assert result.payload["language"] == ["English", "Czech"]


def test_merge_skips_existing_note_and_unchanged_languages():
    current = {**ROW, "notes": "[2026-01-01 renamer] same"}
    result = merge_update(
        current,
        {"notes": "same", "language": ["English"]},
        multi_select_fields={"language"},
        tool="renamer",
    )
    assert result.payload == {}


# ------------------------------------------------------------------ client
@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        mock.get("/fields/table/1/").respond(json=FIELDS)
        mock.get("/rows/table/1/5/").respond(json=ROW)
        yield mock


def test_payload_converts_select_values_and_validates(api):
    client = BaserowClient()
    assert client.to_payload(1, {"country": "Norway", "language": ["Czech"]}) == {
        "country": 10,
        "language": [21],
    }
    with pytest.raises(UnknownOption) as err:
        client.to_payload(1, {"country": "Finland"})
    assert err.value.value == "Finland"
    with pytest.raises(BaserowError, match="Unknown column"):
        client.to_payload(1, {"nope": 1})
    with pytest.raises(BaserowError, match="read-only"):
        client.to_payload(1, {"Last modified": "x"})


def test_update_is_dry_run_by_default_and_logged(api):
    patch = api.patch("/rows/table/1/5/").respond(json={**ROW, "country": {"id": 10}})
    result = BaserowClient().update_row(1, 5, {"country": "Norway"}, tool="renamer")
    assert result.dry_run and not patch.called
    with session_scope() as s:
        entry = s.scalars(select(m.BaserowWriteLog)).one()
        assert entry.dry_run and entry.payload == {"country": "Norway"}


def test_update_with_apply_sends_patch_and_can_be_undone(api):
    patch = api.patch("/rows/table/1/5/").respond(json={**ROW, "date": "2011-08-29"})
    client = BaserowClient(apply=True)
    result = client.update_row(1, 5, {"date": "2011-08-29"}, tool="renamer")
    assert not result.dry_run and patch.call_count == 1
    assert patch.calls[0].request.content == b'{"date":"2011-08-29"}'

    client.undo(result.log_id)
    assert patch.call_count == 2
    assert patch.calls[1].request.content == b'{"date":null}'
    with session_scope() as s:
        assert s.get(m.BaserowWriteLog, result.log_id).undone


def test_retries_on_server_errors(api, monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    route = api.get("/rows/table/1/7/")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={"id": 7})]
    assert BaserowClient().get_row(1, 7) == {"id": 7}
    assert route.call_count == 2


def test_client_errors_are_not_retried(api):
    route = api.get("/rows/table/1/8/").respond(404, json={"error": "ERROR_ROW_DOES_NOT_EXIST"})
    with pytest.raises(BaserowError, match="404"):
        BaserowClient().get_row(1, 8)
    assert route.call_count == 1


def test_merge_overwrites_only_the_named_fields():
    current = {**ROW, "audio_file_location": "/Home-program/old.mp3", "title": "Old title"}
    result = merge_update(
        current,
        {"audio_file_location": "/Home-program/new.mp3", "title": "New title"},
        multi_select_fields={"language"},
        tool="renamer",
        overwrite={"audio_file_location"},
    )
    assert result.payload == {"audio_file_location": "/Home-program/new.mp3"}  # follows the file
    assert result.conflicts == {"title": ("Old title", "New title")}  # a tool never overwrites


def test_merge_appends_only_new_note_lines():
    current = {**ROW, "notes": "Renamer: a.mp3 -> x.mp3\nNote (maced): Home program at Uddhava"}
    result = merge_update(
        current,
        {"notes": "Renamer: a.mp3 -> y.mp3\nNote (maced): Home program at Uddhava"},
        multi_select_fields=set(),
        tool="renamer",
        today=date(2026, 10, 18),
    )
    assert result.payload["notes"].endswith("\n[2026-10-18 renamer] Renamer: a.mp3 -> y.mp3")
    assert result.payload["notes"].count("Home program at Uddhava") == 1
