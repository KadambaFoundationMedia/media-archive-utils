"""Checks on what the model wrote: the rules of tools/content-discoverer/SPECS.md."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MAX_TOPICS = 5
MAX_TOPIC_WORDS = 2
MAX_TITLE_WORDS = 6
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


def check_topics(raw: object, problems: list[str]) -> list[str]:
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


def check(data: dict) -> Content:
    problems: list[str] = []
    topics = check_topics(data.get("topics"), problems)
    if not topics:
        problems.append("No usable topic")
    title, words = check_title(data.get("title"), problems)
    description = check_description(data.get("description"), problems)
    return Content(topics, title, words, description, problems)


def footer(path: Path, today: date | None = None) -> str:
    year = (today or date.today()).year
    return path.read_text(encoding="utf-8").strip().replace("{year}", str(year))


def youtube_description(description: str, footer_path: Path, today: date | None = None) -> str:
    """The description followed by the footer (SPEC.md section 4: always, current year)."""
    return f"{description.strip()}\n\n{footer(footer_path, today)}"


def usable(content: Content) -> bool:
    """Enough to write: topics, a title and a description."""
    return bool(content.topics and content.title_words and content.description)
