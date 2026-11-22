"""The Sanskrit glossary (docs/references/sanskrit-glossary.json) for transcription.

Whisper writes Sanskrit names and terms in many ways ('Harry Krishna', 'Bhagwad Geeta').
The glossary lists each term (`source`) with the spellings Whisper is known to produce
(`variants`), and does two things:

* `prompt()`: a short list of the terms, given to Whisper as its initial prompt (setting
  `TRANSCRIBE_GLOSSARY_PROMPT`, off until measured on real recordings);
* `correct()`: the known variants in a transcript are replaced by the glossary's spelling
  (setting `TRANSCRIBE_GLOSSARY_FIX`, on), so the later tools match one spelling.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

MIN_VARIANT_LETTERS = 5  # shorter variants ('Prem', 'Jap') are too often real words
PROMPT_CATEGORIES = ("Scriptures", "Acharyas / Teachers", "Names of God", "Mantras")
PROMPT_MAX_CHARS = 600


def fold(text: str) -> str:
    """Lower case without diacritics; hyphens count as spaces."""
    text = unicodedata.normalize("NFKD", text.lower().replace("-", " "))
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


@dataclass(frozen=True)
class Glossary:
    entries: tuple[dict, ...]
    pattern: re.Pattern | None
    spelling: dict[str, tuple[str, ...]]  # folded variant -> the glossary's spellings

    def correct(self, text: str) -> tuple[str, int]:
        """`text` with known variants replaced; and how many were replaced."""
        if self.pattern is None:
            return text, 0
        count = 0

        def swap(match: re.Match) -> str:
            nonlocal count
            key = fold(match.group(0))
            choices = self.spelling.get(key)
            if not choices:
                return match.group(0)
            # 'Rādhā / Rādhārāṇī': the part that looks most like what was heard
            wanted = max(choices, key=lambda c: SequenceMatcher(None, key, fold(c)).ratio())
            if fold(wanted) == key:  # already the right spelling (it only guards longer matches)
                return match.group(0)
            count += 1
            return wanted

        return self.pattern.sub(swap, text), count

    def prompt(self) -> str:
        """The terms as one line of plain letters (Whisper copes badly with diacritics)."""
        words: list[str] = []
        for category in PROMPT_CATEGORIES:
            for entry in self.entries:
                if entry.get("category") == category:
                    for part in split_source(entry["source"]):
                        plain = unicodedata.normalize("NFKD", part)
                        words.append("".join(c for c in plain if not unicodedata.combining(c)))
        text = ", ".join(dict.fromkeys(words))
        return text[:PROMPT_MAX_CHARS].rsplit(",", 1)[0] if len(text) > PROMPT_MAX_CHARS else text


def split_source(source: str) -> tuple[str, ...]:
    """'Rādhā / Rādhārāṇī' is two spellings."""
    return tuple(p.strip() for p in source.split(" / ") if p.strip())


def build(entries: list[dict]) -> Glossary:
    spelling: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        source = split_source(entry.get("source") or "")
        for variant in entry.get("variants") or []:
            key = fold(variant)
            if len(re.sub(r"\W", "", key)) < MIN_VARIANT_LETTERS:
                continue
            # a variant that is a spelling of the term itself stays in, so that it protects
            # longer matches ('Srimad Bhagavatam' must not become 'Srimad Śrīmad-Bhāgavatam')
            spelling.setdefault(key, source)
        for part in source:
            if len(re.sub(r"\W", "", fold(part))) >= MIN_VARIANT_LETTERS:
                spelling.setdefault(fold(part), source)
    if not spelling:
        return Glossary(tuple(entries), None, {})
    # every spelling the transcript may have: with a space or a hyphen between words
    alternatives = []
    for key in sorted(spelling, key=len, reverse=True):
        parts = [re.escape(p) for p in key.split()]
        alternatives.append(r"[\s\-]+".join(parts))
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(alternatives) + r")(?![\w])", re.IGNORECASE)
    return Glossary(tuple(entries), pattern, spelling)


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> Glossary:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return build([e for e in data if isinstance(e, dict) and e.get("source")])


def load(path: Path | None) -> Glossary | None:
    """The glossary, or None when the file is missing or unreadable (transcription goes on)."""
    if path is None or not path.is_file():
        return None
    try:
        return _load(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        log.warning("The Sanskrit glossary %s could not be read", path, exc_info=True)
        return None
