"""Class discoverer job: gain, transcribe, find the verse, confirm it on vedabase.io.

Decisions (Edwin, 2026-11-21, see SPEC.md section 4):
- Rules first: the spoken announcement, confirmed by the verse's translation on vedabase.io.
- Sure = announced and confirmed. A sure verse that the filename lacks goes to the Renamer as
  evidence at once; a different verse than the filename's is flagged; everything else (and a
  5% spot check) goes to review.
- No title here (the content-discoverer adds it) and no cutting (only the class-trimmer trims).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import CorruptedMedia, MamError, VedabaseError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import sha256
from mam.helpers.local_db.repo import set_status
from mam.helpers.transcriber import (
    Transcript,
    transcribe,
    transcript_file,
    transcript_quality,
    write_markdown,
)
from mam.helpers.vedabase import VedabaseClient, VerseRef
from mam.manager.tools import JobContext, ToolOutcome
from mam.tools.class_discoverer.announce import (
    Announcement,
    best_announcement,
    find_announcements,
)
from mam.tools.class_discoverer.gain import apply_gain
from mam.tools.class_discoverer.match import VerseMatch, match_translation

log = logging.getLogger(__name__)
TOOL = "class-discoverer"

LANGUAGE_NAMES = {
    "en": "English", "nl": "Dutch", "de": "German", "es": "Spanish", "hi": "Hindi",
    "fr": "French", "cs": "Czech", "sk": "Slovak", "ru": "Russian", "pt": "Portuguese",
    "it": "Italian", "pl": "Polish", "hr": "Croatian", "sl": "Slovenian", "sr": "Serbian",
    "hu": "Hungarian", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian", "fi": "Finnish",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "bn": "Bengali", "ta": "Tamil",
    "uk": "Ukrainian", "ro": "Romanian", "bg": "Bulgarian", "tr": "Turkish", "zh": "Chinese",
}  # fmt: skip


# ---------------------------------------------------------------- the filename
def filename_verse(record: m.MediaFile) -> VerseRef | None:
    """The complete verse the filename (as the Renamer read it) already has, if any."""
    from mam.tools.renamer.parse import find_verse

    proposal = (record.meta or {}).get("proposal") or {}
    for text in (proposal.get("what"), record.current_name, Path(record.original_path or "").name):
        if not text:
            continue
        ref = VerseRef.from_what(text)
        if ref is None:
            found = find_verse(text)
            ref = VerseRef.from_what(found.code) if found else None
        if ref is not None:
            return ref
    return None


def source_audio(record: m.MediaFile, *, dry_run: bool) -> Path:
    path = Path(record.processed_path) if record.processed_path else None
    if path is not None and path.exists():
        return path
    if dry_run and record.original_path and Path(record.original_path).exists():
        return Path(record.original_path)
    raise MamError("Run the Renamer first: the class discoverer works on the renamed copy")


def not_a_class(record: m.MediaFile) -> str | None:
    """Kirtan-only outputs are Skipped for this step (CONTRACTS.md)."""
    if record.derivation == "kirtan":
        return "A kirtan file: the class discoverer does not apply"
    splitter = ((record.meta or {}).get("splitter") or {}).get("proposal") or {}
    if splitter.get("mode") == "kirtan_only":
        return "A kirtan recording (singing throughout): the class discoverer does not apply"
    return None


# ------------------------------------------------------------------ discovery
def _confirm(
    client: VedabaseClient, ref: VerseRef, transcript: Transcript
) -> tuple[bool, VerseMatch | None, str]:
    """(page exists, how well the verse's translation was heard, note) for the first and last
    verse of `ref`. A verse that is not on vedabase.io is a mishearing."""
    segments = [(s.start, s.text) for s in transcript.segments]
    best: VerseMatch | None = None
    verses = ref.verses()
    for single in {verses[0], verses[-1]}:
        verse = client.fetch(single)
        if verse is None:
            return False, None, f"{single.readable} is not on vedabase.io"
        got = match_translation(verse, segments)
        if best is None or got.share > best.share:
            best = got
    assert best is not None
    return True, best, ""


def discover(
    transcript: Transcript, file_ref: VerseRef | None, client: VedabaseClient
) -> dict[str, Any]:
    """The verse evidence, without side effects on files or the database (but fetching and
    caching the verse pages)."""
    found = find_announcements(transcript)
    best, votes = best_announcement(found)
    data: dict[str, Any] = {
        "announcements": [a.to_dict() for a in found[:10]],
        "found": None,
        "file": file_ref.what if file_ref else None,
        "quality": transcript_quality(transcript.segments, transcript.fallback),
    }
    if best is not None and best.ref is not None:
        heard_times = votes[best.ref.what]
        exists, match, note = _confirm(client, best.ref, transcript)
        if not (exists and match and match.confirmed):
            # the speaker may have corrected himself ('chapter 9 ... no, chapter 19'): the last
            # reading is tried first, the first reading is the alternative
            for alt in best.partial.get("alternatives") or []:
                alt_ref = VerseRef.from_what(alt)
                if alt_ref is None:
                    continue
                a_exists, a_match, a_note = _confirm(client, alt_ref, transcript)
                if a_exists and a_match and a_match.confirmed:
                    best = replace(best, ref=alt_ref)
                    exists, match, note = a_exists, a_match, a_note
                    break
        data["found"] = {
            "what": best.ref.what,
            "tag": best.ref.tag,
            "time": best.start,
            "excerpt": best.excerpt,
            "heard": heard_times,
            "on_vedabase": exists,
            "match": match.to_dict() if match else None,
            "note": note,
            "confirmed": exists and bool(match and match.confirmed),
        }
    if file_ref is not None:
        exists, match, note = _confirm(client, file_ref, transcript)
        data["file_match"] = {
            "on_vedabase": exists,
            "match": match.to_dict() if match else None,
            "note": note,
        }
    return data


def decide(data: dict[str, Any]) -> dict[str, Any]:
    """Turn the evidence into a decision: action is 'name' (tell the Renamer), 'keep',
    'none' (no verse in the recording) or 'review'."""
    found, file_what = data["found"], data["file"]
    if found is None:
        if file_what:
            fm = (data.get("file_match") or {}).get("match") or {}
            note = (
                "The filename's verse is heard in the recording."
                if fm.get("confirmed")
                else "No verse was announced; the filename's verse is kept (not confirmed)."
            )
            return {"action": "keep", "sure": True, "reasons": [note]}
        quality = data.get("quality") or {}
        if quality.get("unreliable"):
            return {
                "action": "none",
                "sure": False,
                "reasons": [
                    "No verse was heard, but the transcript looks unreliable "
                    f"({quality.get('bad_share', 0):.0%} of the lines are repeated or invented "
                    "text), so the verse may simply not have been understood. Listen to the "
                    "start of the class."
                ],
            }
        return {
            "action": "none",
            "sure": True,
            "reasons": ["No verse was announced in the recording."],
        }
    same = file_what is not None and VerseRef.from_what(file_what).same_verse_as(  # type: ignore[union-attr]
        VerseRef.from_what(found["what"])  # type: ignore[arg-type]
    )
    if same:
        return {
            "action": "keep",
            "sure": True,
            "reasons": [f"The transcript confirms the filename's verse ({found['tag']})."],
        }
    if file_what:
        return {
            "action": "review",
            "sure": False,
            "reasons": [
                f"The recording announces {found['what']}, but the filename says {file_what}. "
                "The filename is kept until you choose."
            ],
        }
    if found["confirmed"]:
        return {
            "action": "name",
            "sure": True,
            "reasons": [
                f"The recording announces {found['what']} and vedabase.io confirms it "
                f"({found['match']['share']:.0%} of the translation was heard)."
            ],
        }
    why = found["note"] or (
        f"only {found['match']['share']:.0%} of the translation was heard"
        if found["match"]
        else "the verse page could not be compared"
    )
    return {
        "action": "review",
        "sure": False,
        "reasons": [f"The recording announces {found['what']}, but it is not confirmed: {why}."],
    }


def duration_text(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def took(t: dict[str, float]) -> str:
    """'4 min 12 s (volume 3 s, transcription 3 min 58 s, verse check 11 s)'."""
    transcription = (
        "transcript from the cache"
        if not t["transcription_s"]
        else duration_text(t["transcription_s"])
    )
    return (
        f"{duration_text(t['total_s'])} (volume {duration_text(t['gain_s'])}, "
        f"transcription {transcription}, verse check {duration_text(t['verse_s'])})"
    )


def class_start(announcements: list[dict[str, Any]], found: dict[str, Any] | None) -> float | None:
    """When the verse was announced: recorded for the class-trimmer, nothing is cut here."""
    return found["time"] if found else None


# --------------------------------------------------------------------- the job
def _prefetch(client: VedabaseClient, ref: VerseRef | None) -> threading.Thread | None:
    """Fetch the filename's verse pages while the transcription runs: the 10 s crawl delay
    then costs no extra time."""
    if ref is None:
        return None

    def work() -> None:
        try:
            verses = ref.verses()
            for single in {verses[0], verses[-1]}:
                client.fetch(single)
        except Exception:  # the real fetch repeats it and reports a problem
            log.warning("Prefetch of %s failed", ref.readable, exc_info=True)

    thread = threading.Thread(target=work, name="mam-vedabase-prefetch", daemon=True)
    thread.start()
    return thread


def transcript_path(settings: Settings, record: m.MediaFile, audio: Path) -> Path:
    return transcript_file(settings, audio)


def run_class_discoverer(ctx: JobContext) -> ToolOutcome:
    record, settings = ctx.media_file, ctx.settings
    if record is None:
        raise MamError("The class discoverer needs a file")
    skip = not_a_class(record)
    if skip:
        return ToolOutcome("skipped", skip, {})
    started = time.monotonic()
    audio = source_audio(record, dry_run=ctx.dry_run)
    file_ref = filename_verse(record)
    client = VedabaseClient(settings)
    try:
        gain = apply_gain(ctx.session, record, audio, settings, dry_run=ctx.dry_run)
    except CorruptedMedia as exc:
        return ToolOutcome("corrupted", str(exc), {})
    gained = time.monotonic()
    sha = record.sha256 or sha256(audio)
    if not ctx.dry_run:
        record.sha256 = sha
    prefetch = _prefetch(client, file_ref)
    ctx.session.commit()  # the transcription takes minutes; do not hold the database
    transcript, cached = transcribe(ctx.session, audio, sha, settings)
    ctx.session.commit()  # vedabase.io caches pages through its own session: no lock may be held
    transcribed = time.monotonic()
    if prefetch is not None:
        prefetch.join()
    try:
        data = discover(transcript, file_ref, client)
    except VedabaseError as exc:
        # vedabase.io unreachable: keep the transcript, the job can be retried
        raise MamError(f"The transcript is saved, but {exc}") from exc
    finished = time.monotonic()
    timings = {
        "gain_s": round(gained - started, 1),
        "transcription_s": round(transcribed - gained, 1),  # 0 when the transcript was cached
        "verse_s": round(finished - transcribed, 1),
        "total_s": round(finished - started, 1),
    }
    decision = decide(data)
    languages = [LANGUAGE_NAMES.get(code, code) for code in transcript.languages[:3]]
    data.update(
        decision=decision,
        gain=gain,
        language=transcript.language,
        languages=languages,
        class_start=class_start(data["announcements"], data["found"]),
        transcript_cached=cached,
        segments=len(transcript.segments),
        timings=timings,
    )
    summary = "; ".join(decision["reasons"]) + f" Took {took(timings)}."
    if ctx.dry_run:
        return ToolOutcome("info", f"Would: {summary}", data)

    path = write_markdown(transcript, transcript_path(settings, record, audio), audio.stem)
    data["transcript_file"] = str(path)
    meta = dict(record.meta or {})
    meta["class_discoverer"] = {**data, "run_at": datetime.now(UTC).isoformat()}
    record.meta = meta

    spot = random.random() < settings.spot_check_rate
    unsure_none = decision["action"] == "none" and not decision["sure"]
    if decision["action"] == "review" or unsure_none or (spot and decision["action"] == "name"):
        reason = "\n".join(decision["reasons"])
        if spot and decision["sure"]:
            reason = (
                "Random check, nothing is wrong: the class discoverer was sure, but "
                f"{settings.spot_check_rate:.0%} of its sure decisions are shown to you anyway. "
                "Nothing has been changed. " + reason
            )
        _open_review(ctx.session, record, data, reason, spot)
        return ToolOutcome("review", f"{summary} (needs a review)", data)
    if decision["action"] == "none" and not _has_what(record):
        reason = "No verse was announced and the file has no WHAT at all. Choose what it is."
        _open_review(ctx.session, record, data, reason, False)
        return ToolOutcome("review", reason, data)
    if decision["action"] == "name":
        queued = queue_evidence(ctx.session, record, data["found"], languages)
        data["renamer_queued"] = queued
        summary += " The Renamer is queued with this evidence." if queued else ""
    return ToolOutcome("done", summary, data)


def _has_what(record: m.MediaFile) -> bool:
    proposal = (record.meta or {}).get("proposal") or {}
    return bool(proposal.get("what") or proposal.get("category"))


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
    found = data.get("found") or {}
    session.add(
        m.Review(
            media_file_id=record.id,
            tool=TOOL,
            reason=reason,
            proposal={
                "found": data.get("found"),
                "file": data.get("file"),
                "file_match": data.get("file_match"),
                "announcements": data.get("announcements"),
                "languages": data.get("languages"),
                "recommended": "transcript" if found.get("confirmed") else "filename",
                "action": data["decision"]["action"],
            },
            features={"match": found.get("match"), "heard": found.get("heard")},
            score=(found.get("match") or {}).get("share"),
            spot_check=spot_check,
        )
    )


# ---------------------------------------------------------------- the Renamer
def evidence_for(found: dict[str, Any], languages: list[str]) -> dict[str, Any]:
    return {
        "what": found["what"],
        "verse_tag": found["tag"],
        "languages": languages,
        "source": TOOL,
    }


def queue_evidence(
    session: Session, record: m.MediaFile, found: dict[str, Any], languages: list[str]
) -> list[int]:
    """Ask the Renamer to rename with the verse (it never renames itself here). A class
    audio cut from a video also gives its verse to the video's own row (SPEC.md section 4)."""
    from mam.manager.queue import enqueue

    evidence = evidence_for(found, merge_languages(record, languages))
    queued = []
    targets = [record]
    parent = session.get(m.MediaFile, record.parent_id) if record.parent_id else None
    if parent is not None and parent.kind == "video":
        targets = [parent] if not record.original_path else [record, parent]
    for target in targets:
        if target.original_path:
            enqueue(session, "renamer", [target.id], dry_run=False, params={"evidence": evidence})
            queued.append(target.id)
    return queued


def merge_languages(record: m.MediaFile, detected: list[str]) -> list[str]:
    known = list(((record.meta or {}).get("proposal") or {}).get("languages") or ["English"])
    return known + [lang for lang in detected if lang not in known]


# -------------------------------------------------------------------- reviews
def decide_review(
    session: Session,
    review: m.Review,
    *,
    action: str,
    what: str | None,
    operator: str,
    note: str | None,
) -> str:
    """Approve the transcript's verse, keep the filename's, or name another verse."""
    record = session.get(m.MediaFile, review.media_file_id)
    proposal = review.proposal
    found = proposal.get("found")
    languages = proposal.get("languages") or []
    if action == "use_transcript":
        if not found:
            raise MamError("The recording has no announced verse to use")
        chosen = dict(found)
        review.outcome = "approved" if proposal.get("recommended") == "transcript" else "edited"
    elif action == "custom":
        ref = VerseRef.from_what(what)
        if ref is None:
            raise MamError(f"'{what}' is not a verse. Use e.g. BG-2-51 or CC-Madhya-20-154")
        chosen = {"what": ref.what, "tag": ref.tag}
        review.outcome = "edited"
    elif action == "keep_filename":
        review.outcome = "approved" if proposal.get("recommended") == "filename" else "edited"
        review.final = {"keep": proposal.get("file")}
        _close(review, operator, note)
        set_status(session, record, "Class discoverer Done", TOOL, f"filename kept by {operator}")
        return "The filename's verse is kept"
    else:
        raise MamError("Unknown action")
    review.final = {"what": chosen["what"]}
    _close(review, operator, note)
    set_status(session, record, "Class discoverer Done", TOOL, f"{chosen['what']} by {operator}")
    queued = queue_evidence(session, record, chosen, merge_languages(record, languages))
    suffix = " The Renamer is queued." if queued else ""
    return f"Approved: {chosen['what']}.{suffix}"


def _close(review: m.Review, operator: str, note: str | None) -> None:
    review.operator, review.note, review.decided_at = operator, note, datetime.now(UTC)


def transcript_excerpt(session: Session, record: m.MediaFile, start: float | None) -> str:
    """Around `start`: shown on the review page."""
    if start is None or not record.sha256:
        return ""
    rows = session.scalars(
        select(m.TranscriptSegment)
        .where(
            m.TranscriptSegment.content_sha256 == record.sha256,
            m.TranscriptSegment.start_ms >= int(start * 1000) - 20_000,
            m.TranscriptSegment.start_ms <= int(start * 1000) + 60_000,
        )
        .order_by(m.TranscriptSegment.idx)
    ).all()
    return " ".join(r.text for r in rows)


__all__ = ["Announcement", "get_settings", "run_class_discoverer", "decide_review"]
