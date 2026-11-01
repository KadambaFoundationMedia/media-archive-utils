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
