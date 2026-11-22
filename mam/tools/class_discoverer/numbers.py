"""Spoken numbers in English ('fifty-one', 'one hundred and fifty four', 'first') and digits."""

from __future__ import annotations

import re

UNITS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
TENS = {
    w: (i + 2) * 10
    for i, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    )
}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
}  # fmt: skip
TOKEN_RE = re.compile(r"\d+(?:\.\d+)*|[a-z]+", re.IGNORECASE)


ORDINAL_SUFFIX_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    """Lower-case words and numbers; '3rd' is read as 3."""
    text = ORDINAL_SUFFIX_RE.sub(r"\1", text.replace("-", " "))
    return [t.lower() for t in TOKEN_RE.findall(text)]


def parse_number(tokens: list[str], i: int) -> tuple[int, int] | None:
    """The number starting at tokens[i]: (value, index after it), or None."""
    if i >= len(tokens):
        return None
    if tokens[i].isdigit():
        return int(tokens[i]), i + 1
    j, total = i, 0

    def tok(k: int) -> str:
        return tokens[k] if k < len(tokens) else ""

    if tok(j) in UNITS and 1 <= UNITS[tok(j)] <= 9 and tok(j + 1) == "hundred":
        total, j = UNITS[tok(j)] * 100, j + 2
        k = j + 1 if tok(j) == "and" else j
        if tok(k) in UNITS or tok(k) in TENS or tok(k) in ORDINALS:
            j = k
        else:
            return total, j
    if tok(j) in TENS:
        total += TENS[tok(j)]
        j += 1
        if tok(j) in UNITS and 1 <= UNITS[tok(j)] <= 9:
            total, j = total + UNITS[tok(j)], j + 1
        elif tok(j) in ORDINALS and ORDINALS[tok(j)] < 10:
            total, j = total + ORDINALS[tok(j)], j + 1
        return total, j
    if tok(j) in UNITS:
        return total + UNITS[tok(j)], j + 1
    if tok(j) in ORDINALS:
        return total + ORDINALS[tok(j)], j + 1
    return (total, j) if j > i else None
