"""Sanskrit spellings in what the model wrote, taken from the glossary
(docs/references/sanskrit-glossary.json).

The model writes names its own way ("Lord Chaitanya"); the archive writes them the glossary's
way ("Caitanya"). Two steps: known variants become the glossary's spelling (the same step the
transcript gets), and a word that differs from a glossary word only by extra or missing `h`
("Chaitanya", "Bagavatam") becomes that word.
"""

from __future__ import annotations

import re
from typing import Any

from mam.helpers.transcriber.glossary import Glossary, fold, split_source

MIN_LETTERS = 6
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _plain(word: str) -> str:
    return fold(word).replace("h", "")


def word_map(glossary: Glossary) -> dict[str, str]:
    """Without-h key -> the glossary's word, only where that key is unambiguous."""
    seen: dict[str, set[str]] = {}
    for entry in glossary.entries:
        for part in split_source(entry.get("source") or ""):
            for word in WORD.findall(part):
                if len(word) >= MIN_LETTERS:
                    seen.setdefault(_plain(word), set()).add(word)
    return {key: next(iter(words)) for key, words in seen.items() if len(words) == 1}


def fix_text(text: str, glossary: Glossary, words: dict[str, str]) -> str:
    text, _ = glossary.correct(text)

    def swap(match: re.Match) -> str:
        token = match.group(0)
        target = words.get(_plain(token))
        if target is None or fold(token) == fold(target):
            return token
        return target if token[:1].isupper() else target.lower()

    return WORD.sub(swap, text)


def fix_content(data: dict[str, Any], glossary: Glossary | None) -> dict[str, Any]:
    """The model's answer with the glossary's spellings."""
    if glossary is None:
        return data
    words = word_map(glossary)
    out = dict(data)
    if isinstance(data.get("topics"), list):
        out["topics"] = [fix_text(str(t), glossary, words) for t in data["topics"]]
    for key in ("title", "description"):
        if isinstance(data.get(key), str):
            out[key] = fix_text(data[key], glossary, words)
    return out


def terms_in(text: str, glossary: Glossary | None, limit: int = 60) -> list[str]:
    """The glossary spellings of the terms that occur in `text` (for the prompt)."""
    if glossary is None:
        return []
    folded = " " + re.sub(r"[\s\-]+", " ", fold(text)) + " "
    found: list[str] = []
    for entry in glossary.entries:
        parts = split_source(entry.get("source") or "")
        for part in parts:
            key = fold(part)
            if len(key) >= 5 and f" {key} " in folded:
                found.append(part)
                break
    return list(dict.fromkeys(found))[:limit]
