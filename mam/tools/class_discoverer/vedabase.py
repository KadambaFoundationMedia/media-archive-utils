"""Verse look-up on vedabase.io: reference parsing, page parsing and a cached client.

Pages are fetched on demand and cached in the local DB indefinitely (SPEC.md section 1).
vedabase.io's robots.txt asks for a 10 second crawl delay, which the client respects.
Verse ranges have no page of their own: a range is one fetch per verse.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field

import httpx
from bs4 import BeautifulSoup

from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope

log = logging.getLogger(__name__)

BASE_URL = "https://vedabase.io/en/library"
USER_AGENT = "media-archive-utils/1.0 (Kadamba Foundation)"
CRAWL_DELAY_S = 10.0  # robots.txt "Crawl-delay"
LILAS = {"adi": "adi", "madhya": "madhya", "antya": "antya"}
MAX_RANGE = 60  # sanity limit for one range


class VedabaseError(MamError):
    """vedabase.io could not be reached or answered unexpectedly."""


class VerseNotFound(VedabaseError):
    """The verse does not exist on vedabase.io."""


@dataclass(frozen=True)
class VerseRef:
    """One verse: BG ch.v, SB canto.ch.v or CC lila.ch.v."""

    book: str  # "bg" | "sb" | "cc"
    lila: str | None  # CC only: "adi" | "madhya" | "antya"
    numbers: tuple[int, ...]  # BG (ch, v); SB (canto, ch, v); CC (ch, v)

    @property
    def path(self) -> str:
        parts = [self.book] + ([self.lila] if self.lila else []) + [str(n) for n in self.numbers]
        return "/".join(parts)

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.path}/"

    @property
    def tag(self) -> str:
        """The Baserow tag format: numbers only (`2.51`, `Madhya 20.154`)."""
        nums = ".".join(str(n) for n in self.numbers)
        return f"{self.lila.capitalize()} {nums}" if self.lila else nums


_SINGLE_LEN = {"bg": 2, "sb": 3, "cc": 2}


def parse_what(what: str) -> list[VerseRef]:
    """Parse a filename WHAT start (`SB-9-20-20-27-Title`) into the verse(s) it names.

    A range (`SB-9-20-20-27`) is expanded into one VerseRef per verse. Returns [] when the
    text has no valid verse, or the range is not ascending (the file is then flagged).
    """
    toks = [t for t in re.split(r"[-_ ]+", what.strip()) if t]
    if not toks:
        return []
    book = toks[0].lower()
    if book not in _SINGLE_LEN:
        return []
    rest = toks[1:]
    lila = None
    if book == "cc":
        if not rest or rest[0].lower() not in LILAS:
            return []
        lila = LILAS[rest[0].lower()]
        rest = rest[1:]
    nums: list[int] = []
    for t in rest:
        if not t.isdigit():
            break  # the title starts here
        nums.append(int(t))
    n = _SINGLE_LEN[book]
    if len(nums) == n:
        return [VerseRef(book, lila, tuple(nums))]
    if len(nums) == n + 1:
        first, last = nums[n - 1], nums[n]
        if last <= first or last - first >= MAX_RANGE:
            return []
        return [VerseRef(book, lila, (*nums[: n - 1], v)) for v in range(first, last + 1)]
    return []


@dataclass
class Verse:
    path: str
    heading: str
    original: str  # Devanagari or Bengali
    verse_text: str  # Roman transliteration, lines joined by newlines
    synonyms: str
    translation: str
    purport: str = ""
    fetched_at: str = field(default="")


def _section(soup: BeautifulSoup, *classes: str, lines: bool = False) -> str:
    for cls in classes:
        div = soup.select_one(f"div.{cls}")
        if div is None:
            continue
        for h in div.find_all("h2"):
            h.decompose()
        if lines:
            for br in div.find_all("br"):
                br.replace_with("\n")
            text = div.get_text()
            return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
        # keep paragraph breaks, collapse the rest
        paras = [p.get_text(" ", strip=True) for p in div.select("div.copy > div")]
        text = "\n\n".join(p for p in paras if p) or div.get_text(" ", strip=True)
        return re.sub(r"\s+([;,.:])", r"\1", re.sub(r"[ \t]+", " ", text))
    return ""


def parse_verse_html(html: str, path: str = "") -> Verse:
    """Extract the verse sections from a vedabase.io verse page."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    verse = Verse(
        path=path,
        heading=h1.get_text(strip=True) if h1 else "",
        original=_section(soup, "av-devanagari", "av-bengali", lines=True),
        verse_text=_section(soup, "av-verse_text", lines=True),
        synonyms=_section(soup, "av-synonyms"),
        translation=_section(soup, "av-translation"),
        purport=_section(soup, "av-purport"),
    )
    if not verse.verse_text or not verse.translation:
        raise VedabaseError(f"Unexpected vedabase.io page for {path or 'verse'}: no verse text")
    return verse


class VedabaseClient:
    """Fetches verses with a local-DB cache and the robots.txt crawl delay."""

    def __init__(self, delay_s: float = CRAWL_DELAY_S, client: httpx.Client | None = None):
        self.delay_s = delay_s
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        )
        self._last = 0.0
        self._lock = threading.Lock()

    def _wait(self) -> None:
        with self._lock:
            gap = self._last + self.delay_s - time.monotonic()
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()

    def get(self, ref: VerseRef) -> Verse:
        cached = self.cached(ref)
        if cached is not None:
            return cached
        self._wait()
        try:
            resp = self._client.get(ref.url)
        except httpx.HTTPError as exc:
            raise VedabaseError(f"Cannot reach vedabase.io for {ref.path}: {exc}") from exc
        if resp.status_code == 404:
            raise VerseNotFound(f"{ref.path} does not exist on vedabase.io")
        if resp.status_code != 200:
            raise VedabaseError(f"vedabase.io answered {resp.status_code} for {ref.path}")
        verse = parse_verse_html(resp.text, ref.path)
        with session_scope() as s:
            s.merge(m.VedabaseVerse(path=ref.path, data=asdict(verse)))
        log.info("Fetched %s from vedabase.io", ref.path)
        return verse

    def cached(self, ref: VerseRef) -> Verse | None:
        with session_scope() as s:
            row = s.get(m.VedabaseVerse, ref.path)
            return Verse(**row.data) if row else None

    def get_range(self, refs: list[VerseRef]) -> list[Verse]:
        return [self.get(r) for r in refs]
