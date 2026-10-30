"""The Baserow place_country table: the only source of a place's country."""

import pytest
from fastapi.testclient import TestClient

from mam.helpers.local_db import session_scope
from mam.manager.app import create_app
from mam.tools.renamer.editing import check
from mam.tools.renamer.knowledge import build_reference
from mam.tools.renamer.place_country import plan_sync
from tests.renamer_fixtures import reference_raw
from tests.test_editing import save_form
from tests.test_manager import archive, ids, run_queue  # noqa: F401


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


def test_a_place_gets_its_country_only_from_place_country():
    raw = reference_raw()
    assert build_reference(raw).find_place("Brno").cc == "cz"
    raw["place_country"] = [r for r in raw["place_country"] if r["place"] != "Brno"]
    ref = build_reference(raw)
    # the travel schedule has Brno in the Czech Republic, but that is only dated evidence
    assert ref.find_place("Brno").cc is None
    assert any(s.place == "Brno" and s.cc == "cz" for s in ref.stays)
    assert ref.find_place("Zoom-livestream").cc is None  # a general place


def test_unusable_place_country_rows_are_reported():
    raw = reference_raw()
    raw["place_country"] += [
        {"id": 50, "place": "Atlantis", "country": "Nowhere", "cc": ""},
        {"id": 51, "place": "Oslo", "country": "Norway", "cc": "no"},
    ]
    problems = build_reference(raw).place_country_problems
    assert any("Atlantis" in p for p in problems) and any(
        "more than one row" in p for p in problems
    )


def test_saving_a_place_and_country_adds_a_place_country_row(client, imported, no_baserow):
    file_id = imported["R09_0004.MP3"]
    response = client.post(
        f"/files/{file_id}/edit",
        data=save_form(
            "2012-07-12_KKS_Kirtan_Farma-Krishna-Dvur-cz.mp3", place="Farma-Krishna-Dvur", cc="cz"
        ),
        follow_redirects=False,
    )
    assert no_baserow.place_country == [
        {
            "action": "create",
            "values": {
                "place_location": "Farma-Krishna-Dvur",
                "country": "Czech-republic",
                "ccTLD": "cz",
            },
        }
    ]
    assert "now in Czech-republic (place_country)" in response.headers["location"].replace(
        "%20", " "
    )


def test_saving_fills_an_empty_row_and_never_changes_another_country(
    client, imported, no_baserow, reference
):
    reference.place_rows["farma krishna dvur"] = {
        "id": 7,
        "place": "Farma-Krishna-Dvur",
        "country": "",
        "cc": None,
    }
    file_id = imported["R09_0004.MP3"]
    client.post(
        f"/files/{file_id}/edit",
        data=save_form(
            "2012-07-12_KKS_Kirtan_Farma-Krishna-Dvur-cz.mp3", place="Farma-Krishna-Dvur", cc="cz"
        ),
    )
    assert no_baserow.place_country[-1]["action"] == "update"
    assert no_baserow.place_country[-1]["row_id"] == 7
    # Prague is in cz: saving it with 'de' warns and leaves place_country alone
    before = len(no_baserow.place_country)
    form = save_form("2012-07-12_KKS_Kirtan_Prague-de.mp3", place="Prague", cc="de")
    assert "Baserow place_country has Prague in" in check(form, "mp3")["country_warning"]
    client.post(f"/files/{file_id}/edit", data=form)
    # a general place never gets a country
    client.post(
        f"/files/{file_id}/edit",
        data=save_form(
            "2012-07-12_KKS_Kirtan_Zoom-livestream-de.mp3", place="Zoom-livestream", cc="de"
        ),
    )
    assert len(no_baserow.place_country) == before


def test_the_review_page_no_longer_adds_travel_schedule_rows(client):
    assert client.post("/review/1/add-place", data={"place": "X"}).status_code in (404, 405)


def test_sync_plan_one_row_per_place():
    raw = {
        "options": {
            "place_location": [
                "Brno",
                "Amsterdam",
                "Summercamp",
                "Sumercamp",
                "WUSA-House",
                "WUSA-house",
                "Nitai-Mandir",
            ],
            "country": [
                "Czech-republic",
                "Czech-Republic",
                "Netherlands",
                "Serbia",
                "South-Africa",
            ],
        },
        "media": [
            {"id": 1, "place": "Summercamp", "country": "Serbia"},
            {"id": 2, "place": "Summercamp", "country": "Czech-Republic"},
            {"id": 3, "place": "WUSA-House", "country": "South-Africa"},
            {"id": 4, "place": "Nitai-Mandir", "country": "Czech-Republic"},
            {"id": 5, "place": "Nitai-Mandir", "country": None},
            {"id": 6, "place": "Nitai-Mandir", "country": "Czech-republic"},
            {"id": 7, "place": "Nitai-Mandir", "country": "Czech-Republic"},
        ],
        "travel": [{"place_location": "Brno", "country": "Czech-Republic"}],
        "place_country": [{"id": 1, "place": "", "country": "", "cc": ""}],
    }
    plan = plan_sync(raw, ["Zoom-livestream"])
    rows = {r.place: r for r in plan.rows}
    assert (rows["Brno"].country, rows["Brno"].source) == ("Czech-Republic", "travel")
    # the empty row is reused for the first place; the others are new rows
    assert (rows["Amsterdam"].row_id, rows["Amsterdam"].action) == (1, "update")
    assert (rows["Brno"].row_id, rows["Brno"].action) == (None, "create")
    assert (rows["Amsterdam"].cc, rows["Amsterdam"].source) == ("nl", "geography")
    assert rows["Summercamp"].country == ""  # media rows disagree: a general place
    assert rows["WUSA-House"].cc == "za"
    assert "Sumercamp" in plan.skipped and "WUSA-house" in plan.skipped
    fixes = {f.row_id: (f.old, f.new) for f in plan.media}
    assert fixes == {5: (None, "Czech-Republic"), 6: ("Czech-republic", "Czech-Republic")}


def test_place_country_aliases_resolve_to_the_canonical_place():
    raw = reference_raw()
    for row in raw["place_country"]:
        if row["place"] == "Radhadesh":
            row["aliases"] = "New-Radhadesh; RDH ;"
    ref = build_reference(raw)
    for text in ("New-Radhadesh", "rdh", "Radhadesh"):
        place = ref.find_place(text)
        assert place.name == "Radhadesh" and place.cc == "be"
    assert ref.find_place("Brno").name == "Brno"  # rows without aliases are unaffected


def test_an_alias_never_beats_a_real_place():
    raw = reference_raw()
    for row in raw["place_country"]:
        if row["place"] == "Oslo":
            row["aliases"] = "Berlin"
    assert build_reference(raw).find_place("Berlin").name == "Berlin"


def test_aliases_column_is_read_whatever_its_case(monkeypatch):
    """The live Baserow column is `Aliases` (capital A); the row also has Baserow's own keys."""
    from mam.tools.renamer import knowledge

    class Client:
        def place_country(self):
            return [
                {
                    "id": 136,
                    "order": "115.0",
                    "place_location": "Radhadesh",
                    "country": "Belgium",
                    "ccTLD": "be",
                    "Aliases": "NRK;New-Radhakunda-Dham",
                }
            ]

    rows = knowledge._place_country_rows(Client())
    assert rows[0]["aliases"] == "NRK;New-Radhakunda-Dham" and rows[0]["cc"] == "be"
    raw = reference_raw()
    raw["place_country"] = [r for r in raw["place_country"] if r["place"] != "Radhadesh"] + rows
    assert build_reference(raw).find_place("NRK").name == "Radhadesh"
