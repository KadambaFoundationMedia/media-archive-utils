"""Review pages: approve, edit, reject or exclude flagged tool results."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from mam.config import get_settings
from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope
from mam.manager.queue import resume_after_review


def register_review_routes(app: FastAPI, render, notify) -> None:
    @app.get("/reviews", response_class=HTMLResponse)
    def reviews_page(request: Request, show: str = "pending"):
        with session_scope() as session:
            stmt = select(m.Review).order_by(m.Review.id.desc()).limit(500)
            if show == "pending":
                stmt = stmt.where(m.Review.outcome == "pending")
            reviews = session.scalars(stmt).all()
            files = {
                f.id: f
                for f in session.scalars(
                    select(m.MediaFile).where(
                        m.MediaFile.id.in_({r.media_file_id for r in reviews})
                    )
                )
            }
        return render(request, "reviews.html", reviews=reviews, files=files, show=show)

    @app.get("/review/{review_id}", response_class=HTMLResponse)
    def review_page(request: Request, review_id: int, error: str | None = None):
        from mam.tools.renamer.knowledge import get_reference

        with session_scope() as session:
            review = session.get(m.Review, review_id)
            if review is None:
                raise HTTPException(404)
            record = session.get(m.MediaFile, review.media_file_id)
            evidence = session.scalars(
                select(m.WEvidence)
                .where(m.WEvidence.media_file_id == record.id)
                .order_by(m.WEvidence.field, m.WEvidence.score.desc())
            ).all()
            next_id = session.scalar(
                select(m.Review.id)
                .where(m.Review.outcome == "pending", m.Review.id != review_id)
                .order_by(m.Review.id)
            )
        ref = get_reference()
        proposal = review.proposal
        candidates = ref.media_by_date.get(proposal.get("when", ""), [])
        place_names = sorted({p.name for p in ref.places.values()})
        return render(
            request,
            "review.html",
            review=review,
            f=record,
            p=proposal,
            evidence=evidence,
            candidates=candidates,
            places=place_names,
            countries=sorted(ref.country_options.items()),
            next_id=next_id,
            error=error,
        )

    @app.post("/review/{review_id}/preview", response_class=HTMLResponse)
    async def review_preview(request: Request, review_id: int):
        from mam.tools.renamer.tool import preview

        form = await request.form()
        with session_scope() as session:
            review = session.get(m.Review, review_id)
            ext = review.proposal["ext"]
        result = preview(dict(form), ext)
        return render(request, "_name_preview.html", r=result)

    @app.post("/review/{review_id}/decide")
    async def review_decide(request: Request, review_id: int):
        from mam.tools.renamer import tool as renamer

        form = await request.form()
        action = form.get("action")
        operator = get_settings().operator_name
        note = (form.get("note") or "").strip() or None
        messages = {
            "reject": "Result rejected: the copy was removed and the file is Not processed",
            "exclude": "File excluded from further processing",
        }
        result_msg = messages.get(action, "")
        try:
            with session_scope() as session:
                review = session.get(m.Review, review_id)
                if review is None or review.outcome != "pending":
                    raise HTTPException(409, "This review is already decided")
                if action == "approve":
                    record = renamer.approve_review(session, review, dict(form), operator)
                    baserow = (record.meta or {}).get("proposal", {}).get("baserow", {})
                    if baserow.get("dry_run"):
                        result_msg = (
                            "Approved. Baserow is in dry-run mode, so the "
                            f"{'new row' if baserow.get('action') == 'create' else 'row update'} "
                            "was logged on the file page but not written"
                        )
                    else:
                        result_msg = (
                            f"Approved and written to Baserow (row {baserow.get('row_id')})"
                        )
                    if session.scalar(
                        select(m.Job.id).where(
                            m.Job.media_file_id == record.id,
                            m.Job.tool == review.tool,
                            m.Job.pipeline.is_(True),
                        )
                    ):
                        resume_after_review(session, record, review.tool)
                elif action == "reject":
                    renamer.reject_review(session, review, operator, note)
                elif action == "exclude":
                    renamer.exclude_file(session, review, operator, note)
                else:
                    raise HTTPException(400, "Unknown action")
        except MamError as exc:
            return RedirectResponse(f"/review/{review_id}?error={exc}", status_code=303)
        notify(request)
        target = form.get("next") or "/reviews"
        return RedirectResponse(f"{target}?msg={result_msg}", status_code=303)

    @app.post("/review/{review_id}/add-place")
    async def add_place(request: Request, review_id: int):
        """One-click add of a new place to the travel schedule (respects Baserow dry-run)."""
        from mam.helpers.baserow import BaserowClient
        from mam.tools.renamer.knowledge import reset_cache

        form = await request.form()
        place = (form.get("place") or "").strip()
        country = (form.get("country") or "").strip()
        when = (form.get("when") or "").strip()
        if not place:
            raise HTTPException(400, "Place is required")
        row = {
            "place_location": place,
            "country": country,
            "start_date": when if "Y" not in when and "M" not in when and "D" not in when else "",
            "end_date": when if "Y" not in when and "M" not in when and "D" not in when else "",
            "original_schedule": f"Added from the media manager on {date.today().isoformat()}",
        }
        with BaserowClient() as client:
            result = client.create_row(client.travel_table, row, tool="renamer-review")
        reset_cache()
        msg = (
            f"Would add {place} to the travel schedule (Baserow dry-run)"
            if result.dry_run
            else f"Added {place} to the travel schedule"
        )
        return RedirectResponse(f"/review/{review_id}?msg={msg}", status_code=303)
