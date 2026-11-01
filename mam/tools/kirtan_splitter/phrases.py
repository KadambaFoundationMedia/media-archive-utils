"""Mantra types (tools/kirtan-splitter/SPECS.md "Filename convention per mantra types").

The splitter does not listen to words: which mantra was sung is taken from the recording's
scripture (the Renamer's WHAT or category) and can be changed on the review page.
"""

from __future__ import annotations

MAHA_MANTRA = "Kirtan"
RADHA_MADHAVA = "Jaya-radha-madhava"
PANCA_TATTVA = "Jaya-Jaya-Sri-Caitanya"
NRSIMHA = "Nrsimha-pranama"


def mantra_for(what: str | None, category: str | None) -> str | None:
    """Jaya radha-madhava before an SB or BG class, Jaya Jaya Sri Caitanya before a CC class."""
    head = (what or "").split("-")[0].upper()
    if head in ("SB", "BG"):
        return RADHA_MADHAVA
    if head == "CC":
        return PANCA_TATTVA
    cat = (category or "").lower()
    if cat.startswith(("srimad", "bhagavad")):
        return RADHA_MADHAVA
    if cat.startswith(("caitanya", "chaitanya")):
        return PANCA_TATTVA
    return None
