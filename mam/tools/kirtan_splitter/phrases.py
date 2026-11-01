"""Class-start phrases and mantra types (docs/references/recording-common-phrases.md)."""

from __future__ import annotations

import re
import unicodedata

# Mantra type -> filename part (SPECS.md "Filename convention per mantra types").
MAHA_MANTRA = "Kirtan"
RADHA_MADHAVA = "Jaya-radha-madhava"
PANCA_TATTVA = "Jaya-Jaya-Sri-Caitanya"
NRSIMHA = "Nrsimha-pranama"

# Whisper transliterates Sanskrit freely, so words are matched in their common spellings.
_KRSNA = {"krsna", "krishna", "krisna", "krsne", "krishnas"}
MANTRA_VOCAB = _KRSNA | {"hare", "hari", "rama", "haribol", "hari-bol", "jaya", "gaura", "nitai"}


def normalize(text: str) -> str:
    """Lower case, no accents or punctuation, single spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def words(text: str) -> list[str]:
    return normalize(text).split()


# Said by the speaker when the class begins (the first match is the best evidence of the cut).
_END_MARKERS = [
    r"om namo bhagavate vasudeva",
    r"om namo bhagavate vasu deva",
    r"reading from (the )?(srimad|shrimad|bhagavad|chaitanya|caitanya)",
    r"we are reading from",
    r"bhagavad ?gita as it is",
    r"(srimad|shrimad) bhagavatam",
    r"(caitanya|chaitanya) charitamrita",
    r"(caitanya|chaitanya) caritamrta",
    r"\b(adi|madhya|antya) (lila|leela)\b",
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)"
    r" canto\b",
    r"\bcanto \d+",
    r"\bchapter \w+ ?(verse|text)\b",
]
_END_RE = re.compile("|".join(f"(?:{p})" for p in _END_MARKERS))


def end_marker(text: str) -> str | None:
    """The class-start phrase found in `text`, if any."""
    match = _END_RE.search(normalize(text))
    return match.group(0) if match else None


def mantra_share(text: str) -> float:
    """Share of the words that are maha-mantra vocabulary."""
    ws = words(text)
    return sum(w in MANTRA_VOCAB for w in ws) / len(ws) if ws else 0.0


def mantra_type(text: str) -> str | None:
    """Which standard mantra the singing is, from its transcript; None when unclear."""
    ws = set(words(text))
    joined = " ".join(words(text))
    if "radha" in ws and ("madhava" in ws or "madhav" in ws or "radha madhava" in joined):
        return RADHA_MADHAVA
    if ws & {"caitanya", "chaitanya"} and ("nityananda" in ws or "nityananda" in joined):
        return PANCA_TATTVA
    if ws & {"narasimha", "nrsimha", "narsimha", "nrisimha"} and ws & {
        "namaste",
        "namo",
        "pranama",
    }:
        return NRSIMHA
    if "namaste narasimha" in joined or "prahlada" in ws:
        return NRSIMHA
    if joined.count("hare") >= 4 and ws & (_KRSNA | {"rama"}):
        return MAHA_MANTRA
    return None
