"""Build and check filenames (docs/references/filenaming-convention.md)."""

from __future__ import annotations

import re
import unicodedata

from mam.tools.renamer.dates import DateValue

WHO = "KKS"
MAX_LEN = 128
MIN_LEN = 25


def clean_part(text: str) -> str:
    """Keep letters, digits and single hyphens; accents are removed."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def format_where(place: str | None, cc: str | None) -> str | None:
    parts = [clean_part(place)] if place else []
    if cc:
        parts.append(cc.lower())
    where = "-".join(p for p in parts if p)
    return where or None


def build_name(when: DateValue | str, what: str | None, where: str | None, ext: str) -> str:
    when_text = when.text() if isinstance(when, DateValue) else when
    ext = ext.lower().lstrip(".")
    what = clean_part(what) if what else None
    parts = [when_text, WHO] + ([what] if what else []) + ([where] if where else [])
    name = "_".join(parts) + f".{ext}"
    while len(name) > MAX_LEN and what and "-" in what:
        what = what.rsplit("-", 1)[0]
        name = "_".join([when_text, WHO, what] + ([where] if where else [])) + f".{ext}"
    return name


NAME_RE = re.compile(
    r"^(?:\d{4}|YYYY)-(?:\d{2}|MM)-(?:\d{2}|DD)_KKS(?:_[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)*\.[a-z0-9]+$"
)


def problems(name: str) -> list[str]:
    """Convention problems in a filename (empty list when it is valid)."""
    out = []
    if not NAME_RE.match(name):
        out.append("does not match YYYY-MM-DD_KKS[_WHAT][_WHERE].ext")
    if name.count(".") != 1:
        out.append("must contain exactly one dot")
    if len(name) > MAX_LEN:
        out.append(f"longer than {MAX_LEN} characters")
    return out
