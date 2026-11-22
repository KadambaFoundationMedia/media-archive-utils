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
# Share of the verse's Sanskrit (as consonant sequences) heard in a stretch of the transcript at
# which the verse counts as confirmed by the chanting alone (decided 2026-11-22; tune it on real
# samples, see SPEC.md).
SANSKRIT_CONFIRM_SHARE = 0.50
SANSKRIT_WINDOW = 8  # consecutive segments searched together for the chanting
N = 3


def normalize_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text)


def skeleton(text: str) -> str:
    """The consonants of a Sanskrit text as Whisper is likely to hear them.

    Whisper spells chanted Sanskrit by ear ('prasantavasinam akuntamedasam' for 'praśāntam
    āsīnam akuṇṭha-medhasaṁ'), so vowels, aspiration, doubled letters and the near twins m/n,
    v/w/b are ignored, and what is left is compared loosely.
    """
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z]", "", text)
    text = text.replace("sh", "s").replace("h", "")  # aspiration and the visarga are not heard
    text = text.replace("w", "v").replace("b", "v").replace("m", "n")
    text = re.sub(r"[aeiouy]", "", text)
    return re.sub(r"(.)\1+", r"\1", text)


def ngrams(words: list[str], n: int = N) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class VerseMatch:
    share: float  # share of the translation's trigrams found in the transcript, 0..1
    heard_at: float | None  # seconds of the first segment that matches
    confirmed: bool
    sanskrit: float = 0.0  # share of the verse's Sanskrit heard in the best stretch, 0..1
    sanskrit_at: float | None = None

    def to_dict(self) -> dict:
        return {"share": round(self.share, 3), "heard_at": self.heard_at,
                "confirmed": self.confirmed, "sanskrit": round(self.sanskrit, 3),
                "sanskrit_at": self.sanskrit_at}  # fmt: skip


def match_sanskrit(verse: Verse, segments: list[tuple[float, str]]) -> tuple[float, float | None]:
    """(share, seconds): how much of the verse's Sanskrit is heard in the best stretch of the
    transcript, comparing consonant sequences, and where that stretch starts."""
    wanted = ngrams(list(skeleton(verse.transliteration)))
    if not wanted or not segments:
        return 0.0, None
    best, at = 0.0, None
    skeletons = [skeleton(text) for _, text in segments]
    for i in range(len(segments)):
        heard = ngrams(list("".join(skeletons[i : i + SANSKRIT_WINDOW])))
        share = len(heard & wanted) / len(wanted)
        if share > best:
            best, at = share, segments[i][0]
    return best, at


def match_translation(verse: Verse, segments: list[tuple[float, str]]) -> VerseMatch:
    """`segments` is [(start seconds, text)] of the transcript."""
    sanskrit, sanskrit_at = match_sanskrit(verse, segments)
    wanted = ngrams(normalize_words(verse.translation))
    if not wanted:
        return VerseMatch(0.0, None, False, sanskrit, sanskrit_at)
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
    confirmed = share >= CONFIRM_SHARE or sanskrit >= SANSKRIT_CONFIRM_SHARE
    return VerseMatch(share, first, confirmed, sanskrit, sanskrit_at)
