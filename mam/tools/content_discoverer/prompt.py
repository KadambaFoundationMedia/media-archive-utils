"""The prompt for the content discoverer. Bump PROMPT_VERSION when it changes: the cache is
keyed on it, so old answers are not reused for a new prompt."""

from __future__ import annotations

import json
import re
from typing import Any

from mam.helpers.transcriber import Transcript

PROMPT_VERSION = "content-3"

SYSTEM = """\
You describe recordings of Kadamba Kanana Swami, an ISKCON guru, for the Kadamba Foundation's \
archive and YouTube channel. You get the transcript of one recording (a class, lecture, seminar \
or festival address), usually with the verse of scripture it discusses. The transcript was made \
by speech recognition: Sanskrit names may be misspelled, and some lines may be repeated or \
invented. Rely on what is said repeatedly and clearly, and never invent teachings, stories, \
quotes or names that are not in the transcript.

First work out what the recording is really about: where most of its time goes. A class often \
spends little time on the verse and most of it on a discussion, questions or a debate with the \
listeners; the topics, title and description must follow that, with the verse as background. \
Write in English, whatever language is spoken. Return only a JSON object with these keys:

"topics": the topics the speaker really develops, as many as the content needs and no more: \
usually 2 or 3, one for a focused class, 4 or 5 only when that many separate subjects each get \
real time. Fewer, true topics are better than many: a long list dilutes the point of having \
themes. Every topic must be a subject of the teaching that your description also covers, never \
a word that is only mentioned in passing. Each topic is at most 2 words, a natural noun phrase \
in correct English word order ("Humble Inquiry", "Kali Yuga", "Spiritual Authority"; never a \
reversed order such as "Preaching Art" for the art of preaching). Not too general: never use \
a theme from the list of broad themes you are given.

"title": a short title that names the main point of the recording as it is actually spoken. \
2 to 6 words: six is the most you should use, and a seventh word only when the title would \
not be correct English without it (never more than seven). Enticing but accurate, not \
generic, and built around one of the topics where possible. In sentence case (capital \
letters only for names such as Krishna). No verse numbers, no place, no date, no \
quotation marks, no colon.

"description": a YouTube description in 2 to 3 paragraphs separated by a blank line, \
interesting and captivating, that follows the topics. The first sentence says, in natural \
language, what the recording is and when and where it was recorded, using the facts you are \
given, for example: "During a class on Srimad Bhagavatam 1.19.31, recorded on 20 August 2011 \
in Oslo, Norway, Kadamba Kanana Swami considers how ...". Leave out any fact you are not given. \
Plain text, no headings, no bullet points, no hashtags, no footer, no mention that it was \
machine transcribed. Refer to the speaker as Kadamba Kanana Swami or Maharaja.

Spell Sanskrit and Bengali names and terms exactly as in the list of glossary spellings you \
are given, and in the same way as the transcript spells them; never add or drop letters such as \
"h" ("Caitanya", not "Chaitanya").\
"""

SCRIPTURES = {
    "SB": "Srimad Bhagavatam",
    "BG": "Bhagavad-gita",
    "CC": "Caitanya-caritamrta",
}


def spoken_date(rec_date: str | None) -> str | None:
    """'2011-08-20' -> '20 August 2011'; None when the date is not complete."""
    from datetime import date

    try:
        d = date.fromisoformat(rec_date or "")
    except ValueError:
        return None
    return f"{d.day} {d:%B %Y}"


def place_text(place: str | None, cc: str | None) -> str | None:
    """'Oslo' and 'no' -> 'Oslo, Norway'."""
    country = None
    if cc:
        try:
            import pycountry

            found = pycountry.countries.get(alpha_2=cc.upper())
            country = getattr(found, "common_name", None) or getattr(found, "name", None)
        except Exception:  # noqa: BLE001 - the country name is a nicety
            country = None
    place = (place or "").replace("-", " ").strip() or None
    if place and country and place.lower() != country.lower():
        return f"{place}, {country}"
    return place or country


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
    facts: dict[str, str] | None = None,
    broad: list[str] | None = None,
    terms: list[str] | None = None,
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
    for label, key in (("Recorded on", "date"), ("Recorded in", "place")):
        if facts and facts.get(key):
            meta.append(f"{label}: {facts[key]}")
    if facts and facts.get("scripture"):
        meta.append(f"Scripture: {facts['scripture']}")
    if broad:
        meta.append("Broad themes (never use as a topic): " + ", ".join(broad))
    if terms:
        meta.append("Glossary spellings to use for names in the transcript: " + ", ".join(terms))
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


SHORTEN_SYSTEM = """\
You shorten themes (topics) for a recording archive. Each theme you get has more than two \
words. Rewrite each as a theme of at most 2 words that keeps its meaning, as a natural noun \
phrase in correct English word order (for example "Pure Devotional Service" becomes \
"Devotional Service"). Return only a JSON object: {"topics": [...]} with one entry per theme, \
in the same order.\
"""
