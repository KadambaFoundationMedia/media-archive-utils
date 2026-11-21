"""Parse a vedabase.io verse page (div.av-* sections) with the standard library only."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any

SECTIONS = {
    "av-devanagari": "devanagari",
    "av-bengali": "devanagari",
    "av-verse_text": "transliteration",
    "av-synonyms": "synonyms",
    "av-translation": "translation",
    "av-purport": "purport",
}


@dataclass
class Verse:
    heading: str = ""
    devanagari: str = ""
    transliteration: str = ""
    synonyms: list[list[str]] = field(default_factory=list)  # [[sanskrit, english], ...]
    translation: str = ""
    purport: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verse:
        return cls(**data)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: dict[str, list[str]] = {}
        self.heading: list[str] = []
        self._section: str | None = None
        self._depth = 0
        self._skip_h2 = False
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._section is None:
            if tag == "div":
                for cls in classes:
                    if cls in SECTIONS:
                        self._section, self._depth = SECTIONS[cls], 1
                        self.parts.setdefault(self._section, [])
                        return
            if tag == "h1":
                self._in_h1 = True
            return
        if tag == "div":
            self._depth += 1
        elif tag == "h2":
            self._skip_h2 = True
        elif tag in ("br", "p"):
            self.parts[self._section].append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_h1 = False
        if self._section is None:
            return
        if tag == "h2":
            self._skip_h2 = False
        elif tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._section = None
            else:
                self.parts[self._section].append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self.heading.append(data)
        elif self._section is not None and not self._skip_h2:
            self.parts[self._section].append(data)


def _clean(parts: list[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def parse_synonyms(text: str) -> list[list[str]]:
    pairs = []
    for chunk in re.split(r";\s*", text.replace("\n", " ")):
        if "—" in chunk:
            sanskrit, _, english = chunk.partition("—")
            pairs.append([sanskrit.strip(), english.strip()])
    return pairs


def parse_verse_page(html: str) -> Verse | None:
    page = _Page()
    page.feed(html)
    if "translation" not in page.parts and "transliteration" not in page.parts:
        return None
    text = {name: _clean(parts) for name, parts in page.parts.items()}
    return Verse(
        heading=_clean(page.heading),
        devanagari=text.get("devanagari", ""),
        transliteration=text.get("transliteration", ""),
        synonyms=parse_synonyms(text.get("synonyms", "")),
        translation=text.get("translation", ""),
        purport=text.get("purport", ""),
    )
