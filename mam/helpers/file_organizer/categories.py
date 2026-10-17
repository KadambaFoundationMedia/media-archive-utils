"""Match a filename WHAT to a media category and folder via the category_terms_folder table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

IGNORED_CATEGORIES = {"Unknown"}


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens; dots are dropped so 'S.B.' and 'SB' compare equal."""
    text = text.lower().replace(".", "")
    return [t for t in re.split(r"[\s\-_,/]+", text) if t]


@dataclass(frozen=True)
class Category:
    name: str
    folder: str
    color: str | None
    terms: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class CategoryMatch:
    category: Category
    term: str
    score: int


class CategoryMatcher:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.categories: list[Category] = []
        for row in rows:
            name = (row.get("category") or "").strip()
            if not name or name in IGNORED_CATEGORIES:
                continue
            raw_terms = [t for t in (row.get("filename_terms") or "").split(",") if t.strip()]
            terms = {tuple(_tokens(t)) for t in [*raw_terms, name]}
            self.categories.append(
                Category(
                    name=name,
                    folder=(row.get("folder_name") or name).strip(),
                    color=row.get("color"),
                    terms=tuple(t for t in terms if t),
                )
            )

    def by_name(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name.lower() == name.lower()), None)

    def match(self, what: str | None) -> CategoryMatch | None:
        """Best category for a WHAT such as 'SB-9-20-20' or 'CC-Madhya-20-154'.

        Terms must match whole tokens. The earliest match wins (the scripture abbreviation
        leads the WHAT, so 'SB-10-3-11-Janmastami' is Srimad-bhagavatam), longest first at the
        same position. A match that directly follows it refines it to the more specific
        category ('CC' + 'Madhya' -> CC-Madhya-lila).
        """
        if not what:
            return None
        tokens = _tokens(what)
        found: list[tuple[int, int, Category, tuple[str, ...]]] = []
        for category in self.categories:
            for term in category.terms:
                n = len(term)
                for pos in range(len(tokens) - n + 1):
                    if tuple(tokens[pos : pos + n]) == term:
                        found.append((pos, pos + n, category, term))
        if not found:
            return None
        found.sort(key=lambda f: (f[0], -(f[1] - f[0])))
        start, end, category, term = found[0]
        while True:
            follow = [f for f in found if f[0] == end and f[2] is not category]
            if not follow:
                break
            _, end, category, term = max(follow, key=lambda f: f[1])
        return CategoryMatch(category, " ".join(term), end - start)
