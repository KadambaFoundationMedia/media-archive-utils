"""Compare what was said with a verse page from vedabase.io.

The speaker reads the English translation (and often the synonyms) of the verse, so the share
of the translation's three-word sequences that also appear in the transcript is strong evidence
that this is the verse. Whisper spells names slightly differently, so words are compared
without diacritics, punctuation or case.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from mam.helpers.vedabase import Verse

# Share of the translation heard in the transcript at which the verse counts as confirmed.
# Measured on the real samples on Edwin's Mac (see SPEC.md); change it there, not here.
CONFIRM_SHARE = 0.30
N = 3


def normalize_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text)


def ngrams(words: list[str], n: int = N) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class VerseMatch:
    share: float  # share of the translation's trigrams found in the transcript, 0..1
    heard_at: float | None  # seconds of the first segment that matches
    confirmed: bool

    def to_dict(self) -> dict:
        return {"share": round(self.share, 3), "heard_at": self.heard_at,
                "confirmed": self.confirmed}  # fmt: skip


def match_translation(verse: Verse, segments: list[tuple[float, str]]) -> VerseMatch:
    """`segments` is [(start seconds, text)] of the transcript."""
    wanted = ngrams(normalize_words(verse.translation))
    if not wanted:
        return VerseMatch(0.0, None, False)
    heard: set[tuple[str, ...]] = set()
    first: float | None = None
    for start, text in segments:
        found = ngrams(normalize_words(text)) & wanted
        # a sentence is often cut between two segments: also join with the next one
        if found:
            first = start if first is None else first
            heard |= found
    # trigrams spanning a segment boundary
    for (_, a), (start_b, b) in zip(segments, segments[1:], strict=False):
        words = normalize_words(a)[-(N - 1) :] + normalize_words(b)[: N - 1]
        found = ngrams(words) & wanted
        if found:
            first = start_b if first is None else first
            heard |= found
    share = len(heard) / len(wanted)
    return VerseMatch(share, first, share >= CONFIRM_SHARE)
