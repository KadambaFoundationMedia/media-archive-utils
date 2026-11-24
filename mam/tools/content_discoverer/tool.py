"""Content discoverer job: themes, a title and a YouTube description from the transcript.

Decisions (Edwin, 2026-11-24, see SPEC.md section 4):
- The Claude API writes the text (pluggable provider, results cached by input and prompt version).
- Existing themes or descriptions in Baserow are never overwritten: a difference goes to review.
- The first N results (Settings: content_review_first, 20) go to review, then it works alone,
  with the 5% random check.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mam.config import Settings
from mam.errors import MamError, VedabaseError
from mam.helpers.llm import LLMError, cached_reply, get_provider, store_reply
from mam.helpers.local_db import models as m
from mam.helpers.local_db.repo import set_status
from mam.helpers.progress import report
from mam.helpers.transcriber import load_transcript
from mam.helpers.vedabase import VedabaseClient, VerseRef
from mam.manager.tools import JobContext, ToolOutcome
from mam.tools.class_discoverer.tool import filename_verse, not_a_class
from mam.tools.content_discoverer import prompt as P
from mam.tools.content_discoverer.validate import (
    Content,
    check,
    filename_words,
    usable,
    youtube_description,
)

log = logging.getLogger(__name__)
TOOL = "content-discoverer"
PURPORT_WORDS = 400
MAX_NAME = 128


# ------------------------------------------------------------------ the filename
def current_what(record: m.MediaFile) -> str | None:
    proposal = (record.meta or {}).get("proposal") or {}
    return proposal.get("what") or None


def split_what(what: str | None) -> tuple[str, str]:
    """('SB-9-20-20', 'Dont-waste-time') for 'SB-9-20-20-Dont-waste-time'."""
    from mam.tools.renamer.parse import find_verse

    if not what:
        return "", ""
    verse = find_verse(what)
    if verse is None or verse.start != 0:
        return "", what
    return verse.code, what[verse.end :].strip(" -_")


def plan_name(
    what: str | None, category: str | None, title_words: str, ref: VerseRef | None = None
) -> tuple[str | None, str]:
    """The new WHAT with the title added, or None when the filename stays as it is.

    Decided 2026-11-24: only a class with a verse gets a title in its filename. A recording
    without a verse (a festival file named `Vyasa-puja`) keeps its name; its title is only
    stored with the results. A filename that already has a title keeps it.
    """
    verse, rest = split_what(what)
    if not verse and ref is not None:
        verse = ref.what  # the Renamer has not yet written the verse the class discoverer found
    if not verse:
        return None, "The recording has no verse, so the filename is not changed."
    if rest:
        return None, "The filename already has a title, so it is kept."
    if not title_words:
        return None, "No title was written."
    new = f"{verse}-{title_words}"
    return new, f"The title is added to the filename: {new}."


def evidence_for(new_what: str, verse: VerseRef | None, title: str) -> dict[str, Any]:
    spaced = " ".join(filename_words(title).split("-"))
    return {
        "what": new_what,
        "title": f"{verse.readable} {spaced}".strip() if verse else spaced,
        "source": TOOL,
    }


# ---------------------------------------------------------------------- the model
def verse_context(client: VedabaseClient, ref: VerseRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    first = ref.verses()[0]
    try:
        verse = client.fetch(first)
    except VedabaseError:
        log.warning("vedabase.io is not reachable: the verse text is left out", exc_info=True)
        return {"readable": ref.readable}
    if verse is None:
        return {"readable": ref.readable}
    return {
        "readable": ref.readable,
        "translation": verse.translation,
        "purport": P.excerpt(verse.purport, PURPORT_WORDS) if verse.purport else "",
    }


def ask(
    session: Session, settings: Settings, system: str, user: str
) -> tuple[Content, dict[str, Any]]:
    """Cached answer, else the model's; one more try when its answer cannot be used."""
    provider = get_provider(settings)
    usage: dict[str, Any] = {"model": provider.model, "cached": False}
    last_problem = ""
    for attempt in (1, 2):
        reply = cached_reply(session, provider, P.PROMPT_VERSION, system, user)
        if reply is not None:
            usage["cached"] = True
        else:
            reply = provider.complete(system, user, max_tokens=4000)
        try:
            content = check(P.parse_reply(reply.text))
        except ValueError as exc:
            last_problem = str(exc)
            log.warning("Content answer %s not usable: %s", attempt, exc)
            continue
        if not usable(content):
            last_problem = "; ".join(content.problems) or "empty answer"
            continue
        if not usage["cached"]:
            store_reply(session, P.PROMPT_VERSION, system, user, reply)
            session.commit()
        usage.update(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
        return content, usage
    raise LLMError(f"The model's answer could not be used: {last_problem}")


# --------------------------------------------------------------------- Baserow
def content_values(content: Content, settings: Settings) -> dict[str, Any]:
    return {
        "themes": ", ".join(content.topics),
        "media_description": youtube_description(content.description, settings.content_footer_path),
        "youtube_desc": True,
        "notes": f"Content discoverer: themes {', '.join(content.topics)}; title {content.title}",
    }


def write_content(
    record: m.MediaFile,
    values: dict[str, Any],
    settings: Settings,
    *,
    dry_run: bool,
    live: bool = False,
    overwrite: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Fill the media row's themes, description and youtube_desc. Runs follow the "Baserow
    writes" switch; an approval (`live`) always writes. Only empty fields are filled; a field
    in `overwrite` was chosen by a person."""
    from mam.helpers.baserow import BaserowClient
    from mam.helpers.local_db.app_settings import baserow_writes_enabled

    row_id = record.baserow_row_id
    if not row_id:
        return {
            "action": "none",
            "row_id": None,
            "dry_run": True,
            "conflicts": {},
            "note": "The file has no Baserow row yet (the Renamer writes it first).",
        }
    with BaserowClient(settings) as client:
        apply = (baserow_writes_enabled() or live) and not dry_run
        result = client.update_row(
            client.media_table,
            row_id,
            values,
            tool=TOOL,
            media_file_id=record.id,
            dry_run=not apply,
            overwrite=overwrite,
        )
    return {
        "action": "update",
        "row_id": result.row_id,
        "dry_run": result.dry_run,
        "conflicts": {k: list(v) for k, v in (result.conflicts or {}).items() if k != "notes"},
    }


def baserow_summary(baserow: dict[str, Any] | None) -> str:
    if not baserow or baserow.get("action") == "none":
        return "Baserow: no row yet, the results are kept with the file"
    if baserow.get("dry_run"):
        return "Baserow: themes and description logged only (Baserow writes are off)"
    return f"Baserow: themes and description written (row {baserow['row_id']})"


# --------------------------------------------------------------------- the job
def review_count(session: Session) -> int:
    return (
        session.scalar(select(func.count()).select_from(m.Review).where(m.Review.tool == TOOL)) or 0
    )


def run_content_discoverer(ctx: JobContext) -> ToolOutcome:
    record, settings, session = ctx.media_file, ctx.settings, ctx.session
    if record is None:
        raise MamError("The content discoverer needs a file")
    skip = not_a_class(record)
    if skip:
        return ToolOutcome("skipped", skip.replace("class discoverer", "content discoverer"), {})
    started = time.monotonic()
    transcript = load_transcript(session, record.sha256) if record.sha256 else None
    if transcript is None or not transcript.segments:
        raise MamError("Run the Class discoverer first: there is no transcript to read")
    cd = (record.meta or {}).get("class_discoverer") or {}
    report("Reading the transcript", 0.05)
    session.commit()  # vedabase.io caches pages through its own session: no lock may be held

    ref = filename_verse(record)
    if ref is None and (cd.get("found") or {}).get("confirmed"):
        ref = VerseRef.from_what(cd["found"]["what"])
    verse = verse_context(VedabaseClient(settings), ref)
    proposal = (record.meta or {}).get("proposal") or {}
    what = current_what(record)
    category = proposal.get("category")
    text = P.plain_transcript(transcript, cd.get("class_start"))
    user = P.build_user_prompt(
        transcript_text=text,
        verse=verse,
        category=category,
        what=what,
        languages=cd.get("languages") or [],
    )
    ctx.session.commit()  # the model takes a while; do not hold the database
    report("Asking the model", 0.3)
    content, usage = ask(session, settings, P.SYSTEM, user)
    report("Checking the answer", 0.9)

    new_what, name_note = plan_name(what, category, content.title_words, ref)
    values = content_values(content, settings)
    data: dict[str, Any] = {
        "topics": content.topics,
        "title": content.title,
        "title_words": content.title_words,
        "description": content.description,
        "verse": ref.what if ref else None,
        "new_what": new_what,
        "name_note": name_note,
        "problems": content.problems,
        "usage": usage,
        "took_s": round(time.monotonic() - started, 1),
        "prompt_version": P.PROMPT_VERSION,
    }
    cost = ""
    if usage.get("input_tokens"):
        cost = f" ({usage['input_tokens']:,} tokens in, {usage.get('output_tokens') or 0:,} out" + (
            ", from the cache)" if usage["cached"] else ")"
        )
    summary = f"Themes: {', '.join(content.topics)}. Title: {content.title}. {name_note}{cost}"
    if ctx.dry_run:
        data["baserow"] = {"action": "none", "dry_run": True}
        return ToolOutcome("info", f"Would: {summary}", data)

    reasons: list[str] = []
    n_reviews = review_count(session)
    if n_reviews < settings.content_review_first:
        reasons.append(
            f"Trial: the first {settings.content_review_first} results are checked by a person "
            f"before the content discoverer works on its own ({n_reviews + 1} of "
            f"{settings.content_review_first})."
        )
    quality = cd.get("quality") or {}
    if quality.get("unreliable"):
        reasons.append("The transcript looks unreliable (many repeated or invented lines).")
    if content.problems:
        reasons.extend(content.problems)
    spot = False
    if not reasons and random.random() < settings.spot_check_rate:
        spot = True
        reasons.append(
            "Random check, nothing is wrong: "
            f"{settings.spot_check_rate:.0%} of the sure results are shown to you anyway. "
            "Nothing has been written."
        )
    meta = dict(record.meta or {})
    meta["content_discoverer"] = {**data, "run_at": datetime.now(UTC).isoformat()}
    record.meta = meta
    if reasons:
        _open_review(session, record, data, "\n".join(reasons), spot)
        return ToolOutcome("review", f"{summary} (needs a review)", data)

    # Sure: write the themes and description; a different value already in Baserow is a conflict.
    baserow = write_content(record, values, settings, dry_run=False)
    data["baserow"] = baserow
    if baserow["conflicts"]:
        shown = ", ".join(baserow["conflicts"])
        _open_review(
            session,
            record,
            data,
            f"Baserow already has a different value for: {shown}. Nothing was overwritten.",
            False,
        )
        return ToolOutcome("review", f"{summary} Existing {shown} differ (needs a review)", data)
    if new_what:
        data["renamer_queued"] = queue_name(session, record, new_what, ref, content.title)
    return ToolOutcome("done", f"{summary} {baserow_summary(baserow)}.", data)


def queue_name(
    session: Session, record: m.MediaFile, new_what: str, ref: VerseRef | None, title: str
) -> list[int]:
    from mam.manager.queue import enqueue

    if not record.original_path:
        return []
    evidence = evidence_for(new_what, ref, title)
    enqueue(session, "renamer", [record.id], dry_run=False, params={"evidence": evidence})
    return [record.id]


def _open_review(
    session: Session, record: m.MediaFile, data: dict[str, Any], reason: str, spot_check: bool
) -> None:
    for old in session.scalars(
        select(m.Review).where(
            m.Review.media_file_id == record.id,
            m.Review.tool == TOOL,
            m.Review.outcome == "pending",
        )
    ):
        old.outcome = "superseded"
        old.decided_at = datetime.now(UTC)
    session.add(
        m.Review(
            media_file_id=record.id,
            tool=TOOL,
            reason=reason,
            proposal={
                k: data.get(k)
                for k in (
                    "topics",
                    "title",
                    "title_words",
                    "description",
                    "verse",
                    "new_what",
                    "name_note",
                    "problems",
                )
            }
            | {"conflicts": (data.get("baserow") or {}).get("conflicts") or {}},
            features={"usage": data.get("usage")},
            spot_check=spot_check,
        )
    )


# -------------------------------------------------------------------- reviews
def decide_review(
    session: Session,
    review: m.Review,
    *,
    topics: str,
    title: str,
    description: str,
    use_new: set[str],
    operator: str,
    note: str | None,
) -> str:
    """Approve the proposal, with the person's own edits. Approving always writes to Baserow;
    a conflicting existing value is replaced only when the person chose the new one."""
    from mam.config import get_settings
    from mam.helpers.local_db.app_settings import tuned

    settings = tuned(get_settings())
    record = session.get(m.MediaFile, review.media_file_id)
    proposal = review.proposal
    problems: list[str] = []
    from mam.tools.content_discoverer.validate import check_title, check_topics

    new_topics = check_topics([t for t in topics.split(",")], problems)
    new_title, words = check_title(title, problems)
    body = description.strip()
    if not new_topics or not words or not body:
        raise MamError("Topics, title and description cannot be empty. " + " ".join(problems))
    content = Content(new_topics, new_title, words, body)
    edited = (
        new_topics != proposal["topics"]
        or new_title != proposal["title"]
        or body != proposal["description"]
    )
    overwrite = frozenset(
        {"themes": "themes", "description": "media_description"}[k] for k in use_new
    )
    baserow = write_content(
        record,
        content_values(content, settings),
        settings,
        dry_run=False,
        live=True,
        overwrite=overwrite,
    )
    meta = dict(record.meta or {})
    meta["content_discoverer"] = {
        **(meta.get("content_discoverer") or {}),
        "approved": {
            "topics": new_topics,
            "title": new_title,
        },
    }
    record.meta = meta
    ref = VerseRef.from_what(proposal.get("verse"))
    what = current_what(record)
    category = ((record.meta or {}).get("proposal") or {}).get("category")
    new_what, _ = plan_name(what, category, words, ref)
    queued = queue_name(session, record, new_what, ref, new_title) if new_what else []
    review.outcome = "edited" if edited else "approved"
    review.final = {"topics": new_topics, "title": new_title, "new_what": new_what}
    review.operator, review.note, review.decided_at = operator, note, datetime.now(UTC)
    set_status(session, record, "Content discoverer Done", TOOL, f"approved by {operator}")
    suffix = " The Renamer is queued with the title." if queued else ""
    return f"Approved. {baserow_summary(baserow)}.{suffix}"


__all__ = ["run_content_discoverer", "decide_review"]
