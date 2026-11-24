"""Checks on what the model wrote: the rules of tools/content-discoverer/SPECS.md."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MAX_TOPICS = 5
MAX_TOPIC_WORDS = 2
MAX_TITLE_WORDS = 7  # the prompt asks for 6 and allows a 7th only when the grammar needs it
MAX_TITLE_CHARS = 48  # the whole filename stays between 25 and 128 characters


@dataclass
class Content:
    topics: list[str]
    title: str  # as written, for people
    title_words: str  # for the filename: Dont-waste-time
    description: str
    problems: list[str] = field(default_factory=list)  # repaired or dropped, shown on review


def clean_topic(raw: object) -> str:
    text = re.sub(r"[^\w\s'\-]", " ", str(raw or ""), flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip(" -'")


def broad_set(raw: str | None) -> frozenset[str]:
    """The themes that are too general (Settings: "Broad themes"), compared ignoring case and
    hyphens."""
    return frozenset(_fold(w) for w in (raw or "").split(",") if w.strip())


def _fold(text: str) -> str:
    return re.sub(r"[\s\-]+", " ", text.lower()).strip()


def check_topics(
    raw: object, problems: list[str], broad: frozenset[str] = frozenset()
) -> list[str]:
    if not isinstance(raw, list):
        problems.append("The model gave no list of topics")
        return []
    topics: list[str] = []
    for item in raw:
        topic = clean_topic(item)
        if not topic:
            continue
        if len(topic.split()) > MAX_TOPIC_WORDS:
            problems.append(
                f"Topic '{topic}' has more than {MAX_TOPIC_WORDS} words and was dropped"
            )
            continue
        if _fold(topic) in broad:
            problems.append(f"Topic '{topic}' is on the broad themes list and was dropped")
            continue
        if topic.lower() not in {t.lower() for t in topics}:
            topics.append(topic)
    if len(topics) > MAX_TOPICS:
        problems.append(f"{len(topics)} topics were given; the first {MAX_TOPICS} are kept")
        topics = topics[:MAX_TOPICS]
    return topics


def filename_words(title: str) -> str:
    """Title as it goes in a filename: ASCII words joined by hyphens (`Dont-waste-time`)."""
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    ).replace("'", "")
    words = re.findall(r"[A-Za-z0-9]+", ascii_title)
    if words:  # sentence case like `SB-9-20-20-Dont-waste-time`: only the first word is forced
        words[0] = words[0][:1].upper() + words[0][1:]
    return "-".join(words)


def check_title(raw: object, problems: list[str]) -> tuple[str, str]:
    title = re.sub(r"\s+", " ", str(raw or "")).strip().strip("\"'“”")
    if not title:
        problems.append("The model gave no title")
        return "", ""
    words = filename_words(title)
    if len(title.split()) > MAX_TITLE_WORDS or len(words) > MAX_TITLE_CHARS:
        problems.append(f"The title '{title}' is too long for a filename")
        kept = words.split("-")[:MAX_TITLE_WORDS]
        while kept and len("-".join(kept)) > MAX_TITLE_CHARS:
            kept.pop()
        words = "-".join(kept)
    return title, words


def check_description(raw: object, problems: list[str]) -> str:
    text = str(raw or "").replace("\r\n", "\n").strip()
    paragraphs = [
        re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()
    ]
    if not paragraphs:
        problems.append("The model gave no description")
        return ""
    if not 2 <= len(paragraphs) <= 3:
        problems.append(f"The description has {len(paragraphs)} paragraph(s); 2 or 3 are wanted")
    return "\n\n".join(paragraphs)


def check_facts(description: str, facts: dict[str, str], problems: list[str]) -> None:
    """The description names the recording's place and year when they are known."""
    low = description.lower()
    place = facts.get("place")
    if place and place.lower() not in low:
        problems.append(f"The description does not mention the place ({place})")
    year = facts.get("year")
    if year and year not in description:
        problems.append(f"The description does not mention the year ({year})")


def check(
    data: dict, broad: frozenset[str] = frozenset(), facts: dict[str, str] | None = None
) -> Content:
    problems: list[str] = []
    topics = check_topics(data.get("topics"), problems, broad)
    if not topics:
        problems.append("No usable topic")
    title, words = check_title(data.get("title"), problems)
    description = check_description(data.get("description"), problems)
    if description and facts:
        check_facts(description, facts, problems)
    return Content(topics, title, words, description, problems)


def footer(path: Path, today: date | None = None, custom: str = "") -> str:
    """The footer typed on the Settings page, else the text file; `{year}` is the current year."""
    year = (today or date.today()).year
    text = custom.strip() or path.read_text(encoding="utf-8").strip()
    return text.replace("{year}", str(year))


def youtube_description(
    description: str, footer_path: Path, today: date | None = None, custom_footer: str = ""
) -> str:
    """The description followed by the footer (SPEC.md section 4: always, current year)."""
    return f"{description.strip()}\n\n{footer(footer_path, today, custom_footer)}"


def usable(content: Content) -> bool:
    """Enough to write: topics, a title and a description."""
    return bool(content.topics and content.title_words and content.description)
