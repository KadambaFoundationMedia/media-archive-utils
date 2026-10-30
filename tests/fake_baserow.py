"""A stand-in Baserow for manual testing of the media manager (never the live database).

Run:  uv run python -m tests.fake_baserow --port 8799
Then start the portal with BASEROW_API_URL=http://127.0.0.1:8799 and table ids 1/2/3/4
(media, travel schedule, category terms, place_country).
It serves the fixture categories, places, travel schedule and media rows, and accepts
creates and updates (kept in memory and printed).
"""

from __future__ import annotations

import argparse
import itertools

import uvicorn
from fastapi import FastAPI, Request

from tests.renamer_fixtures import reference_raw

RAW = reference_raw()
OPTIONS = RAW["options"]
# Like the live table: the temple and the new city place both exist.
OPTIONS["place_location"] += ["ISKCON-Amsterdam", "Amsterdam"]
OPTIONS["country"] += ["Netherlands"]
MEDIA_FIELDS = [
    {"name": "title", "type": "text"},
    {"name": "date", "type": "date"},
    {"name": "tag", "type": "text"},
    {"name": "notes", "type": "long_text"},
    {"name": "audio_file_location", "type": "text"},
    {"name": "video_file_location", "type": "text"},
    {"name": "imported_on", "type": "date"},
    {"name": "category", "type": "single_select"},
    {"name": "place_location", "type": "single_select"},
    {"name": "country", "type": "single_select"},
    {"name": "language", "type": "multiple_select"},
    {"name": "status_media", "type": "single_select"},
    {"name": "status_thumb", "type": "single_select"},
    {"name": "status_transcript", "type": "single_select"},
]
EXTRA_OPTIONS = {k: ["Not-started"] for k in ("status_media", "status_thumb", "status_transcript")}
ids = itertools.count(1)
FIELDS = []
for field in MEDIA_FIELDS:
    values = OPTIONS.get(field["name"], EXTRA_OPTIONS.get(field["name"], []))
    FIELDS.append({**field, "select_options": [{"id": next(ids), "value": v} for v in values]})
ROWS: dict[int, dict] = {}
# place_country (table 4): one row per place; Amsterdam has no row yet.
PLACE_ROWS: dict[int, dict] = {
    r["id"]: {
        "id": r["id"],
        "place_location": r["place"],
        "country": r["country"],
        "ccTLD": r["cc"],
    }
    for r in RAW["place_country"]
}
PLACE_ROWS[100] = {
    "id": 100,
    "place_location": "ISKCON-Amsterdam",
    "country": "Netherlands",
    "ccTLD": "nl",
    "Aliases": "ATC;Amsterdam-temple-test",  # the live column is spelled `Aliases`
}
BY_ID = {o["id"]: o["value"] for f in FIELDS for o in f["select_options"]}
SELECTS = {f["name"]: f["type"] for f in FIELDS if f["type"].endswith("select")}


def as_baserow(data: dict) -> dict:
    """Select values the way Baserow returns them: {"id", "value"} objects, not bare ids."""
    out = dict(data)
    for name, kind in SELECTS.items():
        value = out.get(name)
        if kind == "single_select" and isinstance(value, int):
            out[name] = {"id": value, "value": BY_ID.get(value)}
        elif kind == "multiple_select" and isinstance(value, list):
            out[name] = [
                {"id": v, "value": BY_ID.get(v)} if isinstance(v, int) else v for v in value
            ]
    return out


row_ids = itertools.count(5000)

app = FastAPI()


def page(rows: list[dict]) -> dict:
    return {"count": len(rows), "next": None, "results": rows}


@app.get("/api/database/fields/table/{table_id}/")
def fields(table_id: int):
    if table_id == 1:
        return FIELDS
    if table_id == 4:
        return [{"name": n, "type": "text"} for n in ("place_location", "country", "ccTLD")]
    if table_id == 2:
        return [
            {"name": n, "type": "text"}
            for n in ("start_date", "end_date", "place_location", "country", "original_schedule")
        ]
    return [
        {"name": n, "type": "text"} for n in ("category", "filename_terms", "folder_name", "color")
    ]


@app.get("/api/database/rows/table/{table_id}/")
def rows(table_id: int):
    if table_id == 2:
        return page(RAW["travel"])
    if table_id == 3:
        return page(RAW["categories"])
    if table_id == 4:
        return page(list(PLACE_ROWS.values()))
    return page(list(ROWS.values()))


@app.get("/api/database/rows/table/{table_id}/{row_id}/")
def row(table_id: int, row_id: int):
    return (PLACE_ROWS if table_id == 4 else ROWS).get(row_id, {"id": row_id})


@app.post("/api/database/rows/table/{table_id}/")
async def create(table_id: int, request: Request):
    data = await request.json()
    row_id = next(row_ids)
    store = PLACE_ROWS if table_id == 4 else ROWS
    store[row_id] = as_baserow({"id": row_id, **data}) if table_id != 4 else {"id": row_id, **data}
    print("CREATE", table_id, row_id, data, flush=True)
    return store[row_id]


@app.patch("/api/database/rows/table/{table_id}/{row_id}/")
async def update(table_id: int, row_id: int, request: Request):
    data = await request.json()
    store = PLACE_ROWS if table_id == 4 else ROWS
    store.setdefault(row_id, {"id": row_id}).update(as_baserow(data) if table_id != 4 else data)
    print("UPDATE", table_id, row_id, data, flush=True)
    return store[row_id]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port, log_level="warning")
