"""Edit a file's name and media-table fields in one place (file page and review page).

The editable **filename** and the media table fields share their values:
- editing the filename fills Date, Place, Country, Tag, Category (and Title while it still
  holds the suggested text) from it;
- editing Date, Place or Country rewrites that part of the filename.

Saving renames the processed copy, writes the media row live (a person's decision) and
records the values as *confirmed*, so later Renamer runs keep them and only fill gaps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mam.config import get_settings
from mam.errors import MamError
from mam.helpers.file_organizer import FileOrganizer
from mam.helpers.local_db import models as m
from mam.helpers.local_db.repo import set_status
from mam.tools.renamer.knowledge import Place, Reference, get_reference, norm_key
from mam.tools.renamer.naming import build_name, format_where, problems
from mam.tools.renamer.parse import NameParser

FIELDS = (
    "filename",
    "when",
    "title",
    "category",
    "tag",
    "place",
    "cc",
    "baserow_row_id",
    "notes",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def exact_place(text: str | None, ref: Reference) -> Place | None:
    """The Baserow place option with exactly this name (case and separators aside).

    In the edit form the typed place is taken literally: no spelling aliases and no
    'ISKCON-' shortcuts, so a new place such as 'Amsterdam' is never swapped for
    'ISKCON-Amsterdam'. (The automatic Renamer does use aliases.)
    """
    if not text:
        return None
    key = norm_key(text)
    for name in ref.place_options:
        if norm_key(name) == key:
            return ref.places.get(key) or Place(name, None, None)
    return None


def place_suggestion(text: str, ref: Reference) -> str | None:
    """A known place the typed text may mean (alias, short name or close spelling)."""
    import difflib

    known = ref.find_place(text)
    if known and norm_key(known.name) != norm_key(text):
        return known.name
    close = difflib.get_close_matches(norm_key(text), list(ref.places), n=1, cutoff=0.8)
    return ref.places[close[0]].name if close else None


def parse_filename(name: str, ref: Reference) -> dict[str, Any]:
    """Values a filename stands for: date, WHAT, place, country, tag, category, title."""
    stem = Path(name).stem
    parsed = NameParser(ref).parse(stem)
    when = "YYYY-MM-DD"
    if parsed.dates and parsed.dates[0].readings:
        when = parsed.dates[0].readings[0].value.text()
    what_parts = ([parsed.verse.code] if parsed.verse else []) + parsed.what_words
    what = "-".join(what_parts) or None
    raw_place = parsed.where_raw or (parsed.places[0].display if parsed.places else "")
    place = exact_place(raw_place, ref)
    match = ref.matcher.match(what)
    title_words = " ".join(parsed.what_words)
    if parsed.verse:
        title = f"{parsed.verse.readable} {title_words}".strip()
    else:
        title = title_words
    return {
        "when": when,
        "what": what,
        "place": place.name if place else raw_place,
        "cc": parsed.ccs[0][0] if parsed.ccs else (place.cc if place else ""),
        "tag": parsed.verse.tag if parsed.verse and parsed.verse.numbers else "",
        "category": match.category.name if match else "",
        "title": title,
    }


def compose_filename(values: dict[str, Any], ext: str, ref: Reference) -> str:
    """Filename from Date, the WHAT in the current filename, Place and Country."""
    current = parse_filename(_clean(values.get("filename")) or f"x.{ext}", ref)
    place_text = _clean(values.get("place"))
    place = exact_place(place_text, ref)
    where = format_where(
        place.display if place else (place_text or None), _clean(values.get("cc")) or None
    )
    return build_name(_clean(values.get("when")) or "YYYY-MM-DD", current["what"], where, ext)


def sync(values: dict[str, Any], changed: str, ext: str) -> dict[str, Any]:
    """Keep the filename and the table fields in step after one field changed."""
    ref = get_reference()
    out = {k: _clean(values.get(k)) for k in FIELDS}
    out["languages"] = list(values.get("languages") or [])
    out["auto_title"] = _clean(values.get("auto_title"))
    if changed == "filename":
        derived = parse_filename(out["filename"], ref)
        current = exact_place(out["place"], ref)
        if current and norm_key(current.display) == norm_key(derived["place"] or ""):
            derived["place"] = current.name  # 'Ljubljana' in the filename is the chosen place
        for key in ("when", "place", "cc", "tag", "category"):
            out[key] = derived[key] or ("" if key != "category" else out["category"])
        if not out["title"] or out["title"] == out["auto_title"]:
            out["title"] = derived["title"]  # only while the title is still the suggestion
        out["auto_title"] = derived["title"]
    elif changed in ("when", "place", "cc"):
        if changed == "place":
            place = exact_place(out["place"], ref)
            if place and place.cc:
                out["cc"] = place.cc  # a known place brings its country
        out["filename"] = compose_filename(out, ext, ref)
    return out | check(out, ext, ref)


def check(values: dict[str, Any], ext: str, ref: Reference | None = None) -> dict[str, Any]:
    """Problems that block saving, and hints about what will be written.

    The keys are `problems` and `hints` (not `notes`: that is a form field).
    """
    from mam.tools.renamer.tool import valid_when

    ref = ref or get_reference()
    issues, hints = [], []
    name = _clean(values.get("filename"))
    if not name:
        issues.append("filename is empty")
    else:
        issues += [f"filename {p}" for p in problems(name)]
        if Path(name).suffix.lower().lstrip(".") != ext:
            issues.append(f"the extension must stay .{ext}")
    if not valid_when(_clean(values.get("when")) or "YYYY-MM-DD"):
        issues.append("date must be YYYY-MM-DD (YYYY, MM and DD may be placeholders)")
    place = _clean(values.get("place"))
    if place and not exact_place(place, ref):
        suggestion = place_suggestion(place, ref)
        hints.append(
            f"'{place}' is a new place: it is not a place_location option in Baserow yet, so "
            "place_location stays empty. Add the option in Baserow (media table, place_location "
            "field), then use 'Refresh Baserow data' on the Settings page and save again."
            + (f" Or did you mean {suggestion}?" if suggestion else "")
        )
    category = _clean(values.get("category"))
    if category and category not in ref.category_options:
        issues.append(f"'{category}' is not a Baserow category")
    return {"problems": issues, "hints": hints}


def initial_values(record: m.MediaFile) -> dict[str, Any]:
    """Form values: the confirmed values, else the last Renamer proposal, else the filename."""
    meta = record.meta or {}
    source = meta.get("confirmed") or meta.get("proposal")
    ref = get_reference()
    if not source and record.original_path:
        from mam.tools.renamer.tool import resolver

        source = resolver().propose(Path(record.original_path)).to_dict()  # read-only
    source = source or {}
    filename = (
        source.get("final_name")
        or source.get("new_name")
        or record.current_name
        or Path(record.original_path or "").name
    )
    derived = parse_filename(filename, ref)
    title = source.get("title") or derived["title"]
    # A title the Renamer suggested may follow the filename; a title someone saved may not.
    auto_title = derived["title"] if meta.get("confirmed") else title
    return {
        "filename": filename,
        "when": source.get("when") or derived["when"],
        "title": title,
        "auto_title": auto_title,
        "category": source.get("category") or derived["category"] or "",
        "tag": source.get("verse_tag") or derived["tag"] or "",
        "place": source.get("place_name") or source.get("place") or derived["place"] or "",
        "cc": source.get("cc") or derived["cc"] or "",
        "languages": source.get("languages") or ["English"],
        "baserow_row_id": source.get("baserow_row_id") or record.baserow_row_id or "",
        "notes": source.get("notes_text") or "",
    }


def save(
    session: Session,
    record: m.MediaFile,
    values: dict[str, Any],
    operator: str,
    review: m.Review | None = None,
) -> dict[str, Any]:
    """Rename the copy, write the media row live, and confirm the values."""
    from mam.tools.renamer.tool import (
        _apply_display,
        _learn_alias,
        reserve_name,
        source_path,
        write_baserow,
    )

    ref = get_reference()
    ext = Path(record.original_path).suffix.lower().lstrip(".")
    result = check(values, ext, ref)
    if result["problems"]:
        raise MamError("; ".join(result["problems"]))
    settings = get_settings()
    parsed = parse_filename(_clean(values["filename"]), ref)
    category = _clean(values.get("category")) or parsed["category"] or None
    place_text = _clean(values.get("place")) or None
    place = exact_place(place_text, ref)
    cc = _clean(values.get("cc")).lower() or None
    old = dict((record.meta or {}).get("proposal") or {})

    session.commit()  # release before helpers write through their own sessions
    organizer = FileOrganizer(ref.matcher, settings)
    folder, _ = organizer.target_folder(parsed["what"], category)
    name, _ = reserve_name(record.id, folder, _clean(values["filename"]))
    placed = organizer.organize(
        source_path(record),
        name,
        parsed["what"],
        tool="manual-edit",
        dry_run=False,
        media_file_id=record.id,
        category=category,
    )
    row_id = _clean(values.get("baserow_row_id"))
    data = {
        **old,
        "original_path": record.original_path,
        "ext": ext,
        "when": _clean(values.get("when")) or "YYYY-MM-DD",
        "what": parsed["what"],
        "place": place.display if place else place_text,
        "place_name": place.name if place else None,
        "cc": cc,
        "where": format_where(place.display if place else place_text, cc),
        "category": category,
        "verse_tag": _clean(values.get("tag")) or None,
        "title": _clean(values.get("title")) or parsed["title"] or Path(name).stem,
        "languages": list(values.get("languages") or ["English"]),
        "notes_text": _clean(values.get("notes")) or None,
        "notes_by": operator,
        "baserow_row_id": int(row_id) if row_id.isdigit() else None,
        "new_name": name,
        "final_name": placed.dst.name,
        "target": str(placed.dst),
        "flags": [],
        "dry_run": False,
    }
    # A person's decision: write the media row live, also when the Baserow switch is off.
    data["baserow"] = write_baserow(record, data, placed.dst, settings, dry_run=False, live=True)

    now = datetime.now(UTC)
    meta = dict(record.meta or {})
    meta["proposal"] = data
    meta["confirmed"] = {**data, "by": operator, "at": now.isoformat()}
    meta["reserved"] = str(placed.dst)
    record.meta = meta
    _apply_display(record, data, placed.dst)
    if data["baserow"].get("row_id"):
        record.baserow_row_id = data["baserow"]["row_id"]

    pending = list(
        session.scalars(
            select(m.Review).where(
                m.Review.media_file_id == record.id, m.Review.outcome == "pending"
            )
        )
    )
    if review is not None and review not in pending:
        pending.append(review)
    for r in pending:
        before = r.proposal or {}
        changed = any(
            str(before.get(k) or "") != str(data.get(k) or "")
            for k in ("when", "what", "place", "cc")
        )
        _learn_alias(session, before.get("place"), data.get("place"))
        r.outcome = "edited" if changed else "approved"
        r.final, r.operator, r.decided_at = data, operator, now
    set_status(session, record, "Renamer Done", "manual-edit", f"saved by {operator}")
    return data
