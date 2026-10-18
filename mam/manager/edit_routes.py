"""File page editing: one form for the filename and the media table fields."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from mam.config import get_settings
from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope


def form_values(form) -> dict:
    values = {k: form.get(k, "") for k in form if k != "languages"}
    values["languages"] = form.getlist("languages")
    return values


def form_context(record: m.MediaFile, values: dict, checked: dict | None = None) -> dict:
    """Everything the edit form needs: values, problems, options and Baserow candidates."""
    from mam.tools.renamer.editing import check
    from mam.tools.renamer.knowledge import get_reference

    ref = get_reference()
    ext = Path(record.original_path).suffix.lower().lstrip(".")
    checked = checked or check(values, ext, ref)
    candidates = ref.media_by_date.get(values.get("when") or "", [])
    return {
        "ev": values,
        "edit_problems": checked["problems"],
        "edit_hints": checked["hints"],
        "candidates": candidates,
        "place_names": sorted(ref.place_options, key=str.casefold),
        "place_warning": checked.get("place_warning", ""),
        "category_options": sorted(ref.category_options),
        "country_options": sorted(ref.country_options.items(), key=lambda kv: kv[1]),
        "language_options": sorted(ref.language_options),
        "ext": ext,
    }


def register_edit_routes(app: FastAPI, render, notify) -> None:
    @app.post("/files/{file_id}/edit/sync")
    async def edit_sync(request: Request, file_id: int) -> JSONResponse:
        """New values for the form after one field changed. Only values are returned: the
        page fills them into the existing fields, so the form and its buttons stay in place."""
        from mam.tools.renamer.editing import sync

        form = await request.form()
        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            ext = Path(record.original_path).suffix.lower().lstrip(".")
        values = sync(form_values(form), form.get("changed", ""), ext)
        problems, hints = values.pop("problems"), values.pop("hints")
        warning = values.pop("place_warning", "")
        return JSONResponse(
            {"values": values, "problems": problems, "hints": hints, "place_warning": warning}
        )

    @app.post("/files/{file_id}/edit")
    async def edit_save(request: Request, file_id: int):
        from mam.tools.renamer.editing import save
        from mam.tools.renamer.tool import baserow_summary

        form = await request.form()
        try:
            with session_scope() as session:
                record = session.get(m.MediaFile, file_id)
                if record is None:
                    raise HTTPException(404)
                data = save(session, record, form_values(form), get_settings().operator_name)
        except MamError as exc:
            return RedirectResponse(f"/files/{file_id}?error={exc}", status_code=303)
        notify(request)
        msg = f"Saved {data['final_name']} — {baserow_summary(data.get('baserow'))}"
        return RedirectResponse(f"/files/{file_id}?msg={msg}", status_code=303)
