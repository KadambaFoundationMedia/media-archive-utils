"""Mantra types (tools/kirtan-splitter/SPECS.md, "Determining the mantra type").

The splitter does not transcribe. A kirtan split off a class is always Jaya-radha-madhava, the
only mantra sung in full before a class (decided 2026-11-24; the short Jaya-Jaya-Sri-Caitanya
intro before a CC class is cut by the class trimmer). A recording that is singing throughout is
a `Kirtan`. The reviewer can pick another type.
"""

from __future__ import annotations

import re
from typing import Any

MAHA_MANTRA = "Kirtan"
RADHA_MADHAVA = "Jaya-radha-madhava"
PANCA_TATTVA = "Jaya-Jaya-Sri-Caitanya"
NRSIMHA = "Nrsimha-pranama"


# A filename that says the recording holds a kirtan too: `SB 1.19.31 (with Radha Madhava)`
# reads as the WHAT `SB-1-19-31-with-radha-madhava`. After the split the class is no longer a
# combination, so the phrase goes (decided 2026-11-22). Which words name a kirtan comes from the
# `category_terms_folder` table (the terms of the kirtan categories), not from this code.
DEFAULT_MANTRAS = [MAHA_MANTRA, RADHA_MADHAVA, PANCA_TATTVA, NRSIMHA]  # until `type` is filled in
_JOINERS = {"with", "and", "plus"}


def without_combination(what: str | None, matcher: Any) -> str | None:
    """The WHAT without a "with <kirtan>" phrase, or None when it has none.

    `matcher` is the Baserow `CategoryMatcher`: the words after with/and/plus must be a term
    of a kirtan category (Kirtan, Jaya-Radha-Madhava, ...) and nothing else."""
    if not what:
        return None
    parts = what.split("-")
    for i, word in enumerate(parts):
        if word.lower() not in _JOINERS:
            continue
        for end in range(len(parts), i + 1, -1):  # the longest phrase first
            phrase = "-".join(parts[i + 1 : end])
            hit = matcher.match(phrase)
            if (
                hit
                and hit.category.kind == "kirtan"
                and hit.score == len([t for t in re.split(r"[\s\-_,/]+", phrase) if t])
            ):
                cleaned = "-".join(parts[:i] + parts[end:])
                return cleaned or None
    return None


def mantra_choices(matcher: Any) -> list[str]:
    """The kirtan types a reviewer can pick: the categories of type kirtan in Baserow."""
    names = matcher.kirtan_names() if matcher is not None else []
    return names or list(DEFAULT_MANTRAS)
