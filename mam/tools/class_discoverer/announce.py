"""Find the verse the speaker announces: 'Srimad-Bhagavatam, first canto, chapter nineteen,
text thirty-one' or 'Bhagavad-gita as it is, chapter 2, text 51'.

Rules only (decided 2026-11-21): the transcript is searched for a book name and the numbers
around it. Every announcement found is a candidate with its time and an excerpt; vedabase.io
confirms the best one (`match.py`).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from mam.helpers.transcriber import Transcript
from mam.helpers.vedabase import VerseRef
from mam.tools.class_discoverer.numbers import ORDINALS, parse_number, tokenize

BOOKS = {
    "BG": re.compile(r"bh?agavad[\s\-]?g[iī]t[aā]|\bgita\b|\bb\.?\s?g\b"),
    "SB": re.compile(r"bh[aā]gavatam|bh[aā]gavata\b|bhagavatham|\bs\.?\s?b\b|\bcanto\b|\bkanta\b"),
    "CC": re.compile(
        r"cait?anya[\s\-]?carit[aā]m[rṛ]?ta|chaitanya[\s\-]?charit|\bc\.?\s?c\b"
        r"|\b(?:adi|madhya|antya)[\s\-]?l[iī]l[aā]"
    ),
}
LILA_RE = re.compile(r"\b(adi|madhya|antya)\b")
NOISE = {"number", "no", "the", "is", "it", "its", "of", "in"}


ANNOUNCE_WINDOW_S = 600.0  # the verse is announced at the beginning of the class
MARKER_RE = re.compile(r"^\W*(translation|purport|synonyms)\W*$", re.IGNORECASE)


def is_section_marker(text: str) -> bool:
    """The speaker announcing a part of the verse ('Translation', 'Purport', 'Synonyms')."""
    return bool(MARKER_RE.match(text.strip()))


@dataclass
class Announcement:
    book: str
    ref: VerseRef | None  # complete reference, else None
    partial: dict[str, Any]
    start: float  # seconds of the segment where it was heard
    excerpt: str
    issue: str | None = None  # why it is not usable (e.g. an incomplete reference)
    quoted: bool = False  # a verse quoted in the purport, not the verse of the class

    def to_dict(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "what": self.ref.what if self.ref else None,
            "tag": self.ref.tag if self.ref else None,
            "partial": self.partial,
            "start": self.start,
            "excerpt": self.excerpt,
            "issue": self.issue,
            "quoted": self.quoted,
        }


def _numbers_after(tokens: list[str], keywords: set[str]) -> list[tuple[str, int, int]]:
    """(keyword, value, position) for each 'chapter 2', 'text fifty one', 'first canto'."""
    found = []
    for i, tok in enumerate(tokens):
        if tok not in keywords:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j] in NOISE:
            j += 1
        number = parse_number(tokens, j)
        if number is not None:
            found.append((tok, number[0], i))
        elif i > 0 and tokens[i - 1] in ORDINALS:  # 'first canto'
            found.append((tok, ORDINALS[tokens[i - 1]], i))
    return found


def _range_end(tokens: list[str], keyword_pos: int, first: int) -> int | None:
    j = keyword_pos + 1
    while j < len(tokens) and tokens[j] in NOISE:
        j += 1
    number = parse_number(tokens, j)
    if number is None:
        return None
    k = number[1]
    if k < len(tokens) and tokens[k] in ("to", "through", "thru", "till", "until", "and"):
        end = parse_number(tokens, k + 1)
        if end and end[0] > first:
            return end[0]
    return None


def read_window(book: str, text: str) -> tuple[VerseRef | None, dict[str, Any], str | None]:
    """The verse numbers spoken around a book name: (complete ref, what was heard, issue)."""
    tokens = tokenize(text)
    lower = text.lower()
    heard: dict[str, Any] = {}
    # 'Bhagavad-gita 2.51' / 'SB 3.6.6' written as digits
    dotted = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){1,3})\b", lower)
    if dotted:
        heard["dotted"] = dotted.group(1)
    kw = _numbers_after(tokens, {"canto", "chapter", "text", "texts", "verse", "verses"})
    values: dict[str, int] = {}
    ends: dict[str, int | None] = {}
    for word, value, pos in kw:
        key = {"canto": "canto", "chapter": "chapter"}.get(word, "verse")
        if key not in values:
            values[key] = value
            if key == "verse":
                ends[key] = _range_end(tokens, pos, value)
    heard.update(values)
    lila = LILA_RE.search(lower)
    if lila:
        heard["lila"] = lila.group(1).capitalize()
    nums: list[int] = []
    if book == "BG":
        if "chapter" in values and "verse" in values:
            nums = [values["chapter"], values["verse"]]
        elif dotted and dotted.group(1).count(".") == 1:
            nums = [int(n) for n in dotted.group(1).split(".")]
    elif book == "SB":
        if {"canto", "chapter", "verse"} <= values.keys():
            nums = [values["canto"], values["chapter"], values["verse"]]
        elif dotted and dotted.group(1).count(".") == 2:
            nums = [int(n) for n in dotted.group(1).split(".")]
    else:
        lila_name = heard.get("lila")
        if lila_name and "chapter" in values and "verse" in values:
            nums = [values["chapter"], values["verse"]]
        elif lila_name and dotted and dotted.group(1).count(".") == 1:
            nums = [int(n) for n in dotted.group(1).split(".")]
    if not nums:
        return None, heard, "The announcement names no complete verse"
    end = ends.get("verse")
    ref = VerseRef(book, tuple(nums), heard.get("lila") if book == "CC" else None, end)
    return ref, heard, None


def find_announcements(
    transcript: Transcript, *, max_seconds: float | None = None
) -> list[Announcement]:
    """Every place where the speaker names a book with verse numbers.

    A verse named after the speaker has started reading the translation or purport, or later
    than the first ten minutes, is a quotation inside the purport, not the verse of the class
    (seen on the 2008-01-04-2 sample): it is marked `quoted`.
    """
    segs = transcript.segments
    out: list[Announcement] = []
    marker_seen = False
    for i, seg in enumerate(segs):
        if max_seconds is not None and seg.start > max_seconds:
            break
        if is_section_marker(seg.text):
            marker_seen = True
            continue
        text = seg.text.lower()
        for book, pattern in BOOKS.items():
            match = pattern.search(text)
            if not match:
                continue
            # the numbers are spoken in the same or the next two segments
            window = " ".join(s.text for s in segs[max(0, i - 1) : i + 3])
            ref, heard, issue = read_window(book, window)
            quoted = ref is not None and (marker_seen or seg.start > ANNOUNCE_WINDOW_S)
            out.append(Announcement(book, ref, heard, seg.start, window[:300], issue, quoted))
    return out


def best_announcement(found: list[Announcement]) -> tuple[Announcement | None, Counter]:
    """The complete announcement heard most often (the earliest on a tie)."""
    complete = [a for a in found if a.ref is not None and not a.quoted]
    if not complete:
        return None, Counter()
    votes: Counter = Counter(a.ref.what for a in complete)  # type: ignore[union-attr]
    top = max(votes.values())
    for a in complete:
        if votes[a.ref.what] == top:  # type: ignore[union-attr]
            return a, votes
    return None, votes
