"""File page editing: one form for the filename and the media table fields."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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
        "edit_notes": checked["notes"],
        "candidates": candidates,
        "place_names": sorted({p.name for p in ref.places.values()}),
        "category_options": sorted(ref.category_options),
        "country_options": sorted(ref.country_options.items(), key=lambda kv: kv[1]),
        "language_options": sorted(ref.language_options),
        "ext": ext,
    }


def register_edit_routes(app: FastAPI, render, notify) -> None:
    @app.post("/files/{file_id}/edit/sync", response_class=HTMLResponse)
    async def edit_sync(request: Request, file_id: int):
        from mam.tools.renamer.editing import sync

        form = await request.form()
        with session_scope() as session:
            record = session.get(m.MediaFile, file_id)
            if record is None:
                raise HTTPException(404)
            ext = Path(record.original_path).suffix.lower().lstrip(".")
            values = sync(form_values(form), form.get("changed", ""), ext)
            checked = {"problems": values.pop("problems"), "notes": values.pop("notes")}
            context = form_context(record, values, checked)
            mode = form.get("mode", "file")
            review_id = form.get("review_id") or None
        return render(
            request, "_edit_form.html", f=record, mode=mode, review_id=review_id, **context
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
