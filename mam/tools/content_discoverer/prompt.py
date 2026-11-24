"""The prompt for the content discoverer. Bump PROMPT_VERSION when it changes: the cache is
keyed on it, so old answers are not reused for a new prompt."""

from __future__ import annotations

import json
import re
from typing import Any

from mam.helpers.transcriber import Transcript

PROMPT_VERSION = "content-1"

SYSTEM = """\
You describe recordings of Kadamba Kanana Swami, an ISKCON guru, for the Kadamba Foundation's \
archive and YouTube channel. You get the transcript of one recording (a class, lecture, seminar \
or festival address), usually with the verse of scripture it discusses. The transcript was made \
by speech recognition: Sanskrit names may be misspelled, and some lines may be repeated or \
invented. Rely on what is said repeatedly and clearly, and never invent teachings, stories, \
quotes or names that are not in the transcript.

Write in English, whatever language is spoken. Return only a JSON object with these keys:

"topics": 1 to 5 topics that the speaker really develops. Each topic is at most 2 words. \
Not too general: "Krishna Prema" or "Kali Yuga" is good, "Devotion" or "Spiritual life" is too \
broad. Most important first.

"title": a short title that names the main point of the class. 2 to 6 words, enticing but \
accurate, not generic, and built around one of the topics where possible. In sentence case \
(capital letters only for names such as Krishna). No verse numbers, \
no place, no date, no quotation marks, no colon.

"description": a YouTube description in 2 to 3 paragraphs separated by a blank line, interesting \
and captivating, that follows the topics. Plain text, no headings, no bullet points, no hashtags, \
no footer, no mention that it was machine transcribed. Refer to the speaker as Kadamba Kanana \
Swami or Maharaja.\
"""


def plain_transcript(transcript: Transcript, start: float | None = None) -> str:
    """The spoken text without timestamps, one paragraph per minute so it stays readable.
    From `start` seconds on (the verse announcement) when known: what comes before it is the
    opening mantra and greetings."""
    lines: list[str] = []
    minute = -1
    for seg in transcript.segments:
        if start is not None and seg.end < start - 30:
            continue
        text = seg.text.strip()
        if not text:
            continue
        slot = int(seg.start // 60)
        if slot != minute:
            lines.append("")
            minute = slot
        lines[-1] = f"{lines[-1]} {text}".strip()
    return "\n\n".join(line for line in lines if line)


def build_user_prompt(
    *,
    transcript_text: str,
    verse: dict[str, Any] | None,
    category: str | None,
    what: str | None,
    languages: list[str],
    topics_known: str | None = None,
) -> str:
    parts: list[str] = []
    meta = []
    if category:
        meta.append(f"Category: {category}")
    if what:
        meta.append(f"Filename subject: {what}")
    if languages:
        meta.append("Languages heard: " + ", ".join(languages))
    if topics_known:
        meta.append(f"Themes already entered by a person (keep close to these): {topics_known}")
    if meta:
        parts.append("\n".join(meta))
    if verse:
        text = [f"Verse discussed: {verse['readable']} (vedabase.io)"]
        if verse.get("translation"):
            text.append(f"Translation: {verse['translation']}")
        if verse.get("purport"):
            text.append(f"Start of the purport: {verse['purport']}")
        else:
            text.append("This verse has no purport: the themes come from the transcript.")
        parts.append("\n".join(text))
    else:
        parts.append("No verse was identified for this recording.")
    parts.append("Transcript:\n" + transcript_text)
    return "\n\n".join(parts)


def excerpt(text: str, words: int) -> str:
    found = text.split()
    return " ".join(found[:words]) + (" ..." if len(found) > words else "")


def parse_reply(text: str) -> dict[str, Any]:
    """The JSON object in the model's answer (it may be wrapped in a code fence)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data
