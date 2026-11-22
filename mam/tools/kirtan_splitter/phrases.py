"""Mantra types (tools/kirtan-splitter/SPECS.md, "Determining the mantra type").

The splitter does not transcribe. The kirtan is named from what the Renamer found for the
class: SPECS.md says Jaya-radha-madhava is always sung before a Srimad-bhagavatam or
Bhagavad-gita class and the Panca-tattva mantra before a Caitanya-caritamrta class. Anything
else is named `Kirtan` and the reviewer can pick another type.
"""

from __future__ import annotations

import re

MAHA_MANTRA = "Kirtan"
RADHA_MADHAVA = "Jaya-radha-madhava"
PANCA_TATTVA = "Jaya-Jaya-Sri-Caitanya"
NRSIMHA = "Nrsimha-pranama"


def mantra_for_what(what: str | None) -> str | None:
    """Mantra type implied by the class's WHAT (`SB-3-6-6`, `BG-2-51`, `CC-Madhya-20-154`)."""
    if not what:
        return None
    book = re.split(r"[-_ ]", what.strip(), maxsplit=1)[0].upper()
    if book in ("SB", "BG"):
        return RADHA_MADHAVA
    if book == "CC":
        return PANCA_TATTVA
    return None


# A filename that says the recording holds a kirtan too: `SB 1.19.31 (with Radha Madhava)`
# reads as the WHAT `SB-1-19-31-with-radha-madhava`. After the split the class is no longer a
# combination, so the phrase goes (decided 2026-11-22).
_COMBINATION = re.compile(
    r"-(?:with|and|plus)-(?:the-)?(?:jaya-)?(?:jaya-)?"
    r"(?:radha-madhava|sri-caitanya|kirtans?|bhajans?|nrsimha-pranama|maha-mantra)(?=-|$)",
    re.IGNORECASE,
)


def without_combination(what: str | None) -> str | None:
    """The WHAT without a "with <kirtan>" phrase, or None when it has none."""
    if not what:
        return None
    cleaned = _COMBINATION.sub("", what)
    return cleaned if cleaned != what and cleaned else None
