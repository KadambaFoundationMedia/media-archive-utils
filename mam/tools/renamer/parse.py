"""Parse a file or folder name into evidence: dates, verse, WHAT words, places, countries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mam.tools.renamer.countries import cc_for_name, is_valid_cctld
from mam.tools.renamer.dates import DateMatch, find_dates
from mam.tools.renamer.knowledge import Place, Reference, norm_key

# ------------------------------------------------------------------- verses
BOOK_RE = re.compile(
    r"(?<![A-Za-z])(?P<book>S\.\s?B\.?|SB|B\.\s?G\.?|BG|C\.\s?C\.?|CC|Srimad[\s\-_]?Bhagavatam"
    r"|Bhagavad[\s\-_]?Gita)(?![A-Za-z])",
    re.IGNORECASE,
)
LILA_RE = re.compile(r"(?P<lila>Adi|Madhya|Antya)(?:[\s\-_]?lila)?(?![A-Za-z])", re.IGNORECASE)
LILA_ONLY_RE = re.compile(
    r"(?<![A-Za-z])(?P<lila>Adi|Madhya|Antya)(?:[\s\-_]?lila)?(?=[\s\-_.]*\d)", re.IGNORECASE
)
NUM_RE = re.compile(r"[\s.,\-_]{0,3}(\d{1,3})(?!\d)")
RANGE_RE = re.compile(r"-(\d{1,3})(?!\d)(?![.\-]\d)")
VERSE_LENGTH = {"BG": 2, "SB": 3, "CC": 2}  # numbers after the book (CC: after the lila)
MAX_RANGE = 40


def _book_code(raw: str) -> str:
    key = re.sub(r"[^a-z]", "", raw.lower())
    if key.startswith(("sb", "srimad")):
        return "SB"
    if key.startswith(("bg", "bhagavad")):
        return "BG"
    return "CC"


@dataclass
class Verse:
    book: str
    lila: str | None
    numbers: list[int]
    range_end: int | None
    start: int
    end: int
    raw: str

    @property
    def code(self) -> str:
        parts = [self.book] + ([self.lila] if self.lila else []) + [str(n) for n in self.numbers]
        if self.range_end is not None:
            parts.append(str(self.range_end))
        return "-".join(parts)

    @property
    def tag(self) -> str:
        """Verse as stored in the Baserow `tag` column, e.g. '3.6.6' or 'Madhya 20.154'."""
        nums = ".".join(str(n) for n in self.numbers)
        if self.range_end is not None:
            nums += f"-{self.range_end}"
        return f"{self.lila} {nums}" if self.lila else nums

    @property
    def readable(self) -> str:
        """Verse for titles: 'BG 2.54', 'SB 9.20.20-27', 'CC Madhya 20.154'."""
        return f"{self.book} {self.tag}".strip() if self.numbers else self.book

    @property
    def complete(self) -> bool:
        return len(self.numbers) >= VERSE_LENGTH[self.book]


def find_verse(text: str) -> Verse | None:
    match = BOOK_RE.search(text)
    lila = None
    if match:
        book, pos, start = _book_code(match.group("book")), match.end(), match.start()
        if book == "CC":
            lila_match = LILA_RE.match(text, pos + len(text[pos:]) - len(text[pos:].lstrip(" .-_")))
            if lila_match:
                lila, pos = lila_match.group("lila").capitalize(), lila_match.end()
    else:
        match = LILA_ONLY_RE.search(text)
        if not match:
            return None
        book, lila, pos, start = "CC", match.group("lila").capitalize(), match.end(), match.start()

    numbers: list[int] = []
    wanted = VERSE_LENGTH[book] + (1 if book == "CC" and lila is None else 0)
    while len(numbers) < wanted:
        num = NUM_RE.match(text, pos)
        if not num:
            break
        numbers.append(int(num.group(1)))
        pos = num.end()
    if book == "CC" and lila is None and len(numbers) == 3:
        lila = {1: "Adi", 2: "Madhya", 3: "Antya"}.get(numbers[0])
        numbers = numbers[1:] if lila else numbers[:2]
    if not numbers and lila is None and text[:start].strip(" -_.|"):
        return None  # 'Sai-Baba-Program-BG-Talk': BG mid-title is a word, not a verse
    range_end = None
    if len(numbers) == VERSE_LENGTH[book]:
        rng = RANGE_RE.match(text, pos)
        if rng and 0 < int(rng.group(1)) - numbers[-1] <= MAX_RANGE:
            range_end, pos = int(rng.group(1)), rng.end()
    return Verse(book, lila, numbers, range_end, start, pos, text[start:pos])


# ------------------------------------------------------------ recorder names
RECORDER_RES = [
    re.compile(
        r"^(?:R\d{2}|ZOOM|STE|DSC|MVI|IMG|REC|DR|LS|VN|WS|TASCAM|DM|MOV)[-_ ]?\d{2,5}$", re.I
    ),
    re.compile(r"^\d{5}$"),  # camcorder clips (00008.MTS)
]
PREFIX_RES = [
    re.compile(r"(?<=^\d{6})_\d{4}(?:_D)?"),  # TASCAM 150905_0124_D: keep the date, drop the index
    re.compile(r"^A\d{3}F?(?=\s)"),  # catalogue numbers: A019 03-10-23 ...
    re.compile(r"^\d{1,2}(?=\s)"),  # track numbers: 06 KKS SB 3.1.25
    re.compile(r"\s*\(\d+\)$"),  # copy markers: name (1)
    re.compile(r"(?<=[A-Za-z])-\d{3}$"),  # trailing clip index: _sr-006
    re.compile(r"(?<=_)D$"),  # TASCAM suffix
    re.compile(r"(?<=\d)\s\d{1,2}[.:]\d{2}[.:]\d{2}$"),  # clock time after a date
]
KKS_RE = re.compile(
    r"(?<![A-Za-z])(?:H\.?\s?H\.?\s+)?(?:Kadamba\s*kanana(?:\s*Swami)?|Kadamba\s+S\.?(?=[\s\-_.]|$)|KKS)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
SPLIT_RE = re.compile(r"[\s_\-.,()|]+")
CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z][a-z])")
CONVENTION_DATE_RE = re.compile(
    r"^(?:19|20|YY)\w\w-(?:\d\d|MM|XX)-(?:\d\d|DD|XX)(?:-\d{1,2})?$", re.IGNORECASE
)
CC_SEG_RE = re.compile(r"^(?P<place>.+?)-(?P<cc>[A-Za-z]{2,3})$")


@dataclass
class ParsedName:
    text: str
    source: str  # filename | folder
    convention: bool = False
    dates: list[DateMatch] = field(default_factory=list)
    verse: Verse | None = None
    what_words: list[str] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    unknown_place: str | None = None  # text in the WHERE position that is not a known place
    ccs: list[tuple[str, str]] = field(default_factory=list)  # (cc, raw text)
    has_kks: bool = False
    speakers: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    recorder: bool = False

    @property
    def descriptive(self) -> bool:
        return self.verse is not None or bool(self.what_words)


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = "|"
    return "".join(chars)


def _country_token(token: str, ref: Reference) -> str | None:
    cc = cc_for_name(token)
    if cc:
        return cc
    return None


def _tidy_word(word: str) -> str:
    if len(word) > 3 and word.isupper():
        return word.capitalize()
    return word


class NameParser:
    def __init__(self, ref: Reference) -> None:
        self.ref = ref
        a = ref.aliases
        self.noise = {norm_key(w) for w in a.get("noise", [])}
        self.generic = {norm_key(w) for w in a.get("generic_with_verse", [])}
        self.other_speakers = {w.upper() for w in a.get("other_speakers", [])}

    def parse(self, text: str, source: str = "filename") -> ParsedName:
        text = text.strip()
        result = ParsedName(text=text, source=source)
        if any(r.match(text) for r in RECORDER_RES):
            result.recorder = True
            return result
        cleaned = text
        for pattern in PREFIX_RES:
            if pattern.search(cleaned):
                cleaned = pattern.sub(lambda mt: "|" * len(mt.group(0)), cleaned)
                if pattern is PREFIX_RES[0]:
                    result.recorder = True

        segments = [s.strip() for s in cleaned.split("_")]
        if source == "filename" and len(segments) >= 2 and CONVENTION_DATE_RE.match(segments[0]):
            self._parse_convention(result, segments)
        else:
            self._parse_free(result, cleaned)
        result.languages = list(dict.fromkeys(result.languages))
        return result

    # ------------------------------------------------------------ helpers
    def _spans(self, text: str, result: ParsedName) -> str:
        """Find verse, dates and KKS in `text`; return it with those parts masked."""
        spans: list[tuple[int, int]] = []
        verse = find_verse(text)
        if verse and result.verse is None:
            result.verse = verse
            spans.append((verse.start, verse.end))
        dates = find_dates(text, masked=spans)
        result.dates.extend(dates)
        spans += [(d.start, d.end) for d in dates]
        for kks in KKS_RE.finditer(text):
            result.has_kks = True
            spans.append(kks.span())
        return _mask(text, spans)

    def _classify(
        self,
        tokens: list[str],
        result: ParsedName,
        allow_places: bool = True,
        allow_countries: bool = True,
    ) -> list[str]:
        """Pull places, countries, speakers and languages out of `tokens`; return the rest."""
        used = [False] * len(tokens)
        keys = [norm_key(t) for t in tokens]
        if allow_places:
            for size in range(self.ref.max_place_words, 0, -1):
                for i in range(len(tokens) - size + 1):
                    if any(used[i : i + size]):
                        continue
                    key = " ".join(keys[i : i + size])
                    place = self.ref.places.get(key)
                    if place and not (size == 1 and len(key) <= 2):
                        result.places.append(place)
                        for j in range(i, i + size):
                            used[j] = True
        rest: list[str] = []
        for i, token in enumerate(tokens):
            if used[i]:
                continue
            key = keys[i]
            if token.upper() in self.other_speakers and token.isupper():
                result.speakers.append(token.upper())
                continue
            if key in self.noise:
                continue
            language = self.ref.language(token)
            if language and (token.isupper() or len(key) > 3):
                result.languages.append(language)
            use_cc = allow_countries and len(key) >= 3 and not language
            cc = _country_token(token, self.ref) if use_cc else None
            if cc:
                result.ccs.append((cc, token))
                continue
            rest.append(token)
        for size in (3, 2, 1):
            for i in range(len(rest) - size + 1):
                hint = self.ref.category_hint(" ".join(rest[i : i + size]))
                if hint:
                    result.categories.append(hint)
        return rest

    @staticmethod
    def _tokens(text: str) -> list[str]:
        # Split glued words: RadhastamiBhajanas -> Radhastami Bhajanas
        text = CAMEL_RE.sub(" ", text)
        return [t for t in SPLIT_RE.split(text) if t and t.strip("|")]

    def _words(self, tokens: list[str], result: ParsedName) -> list[str]:
        # Single letters are dropped (BG X.X.), except Roman numerals I and V (CC-Course-V).
        words = [
            _tidy_word(t) for t in tokens if not (len(t) == 1 and t.isalpha() and t not in "IV")
        ]
        if result.verse:
            words = [w for w in words if norm_key(w) not in self.generic]
        if not any(not w.isdigit() for w in words):
            return []  # only index numbers left
        return words

    # --------------------------------------------------------- convention
    def _parse_convention(self, result: ParsedName, segments: list[str]) -> None:
        result.convention = True
        self._spans(segments[0], result)
        who, *rest = segments[1:]
        who_parts = who.split("-")
        what_parts: list[str] = []
        while who_parts and (who_parts[0] == "KKS" or who_parts[0] in self.other_speakers):
            code = who_parts.pop(0)
            if code == "KKS":
                result.has_kks = True
            else:
                result.speakers.append(code)
        what_parts += ["-".join(who_parts)] if who_parts else []
        if not result.has_kks and not result.speakers:
            what_parts = [who, *what_parts[1:]] if what_parts else [who]

        where_text, cc = None, None
        segs = [s for s in rest if s]
        if segs and re.fullmatch(r"[A-Za-z]{2}", segs[-1]):
            cc = segs.pop().lower()
        elif segs:
            m = CC_SEG_RE.match(segs[-1])
            if m and (is_valid_cctld(m.group("cc")) or cc_for_name(m.group("cc"))):
                segs.pop()
                where_text, cc = m.group("place"), m.group("cc")
        # The WHAT can be glued to the WHO (KKS-SB-6-1-15); then one remaining segment is WHERE.
        what_in_who = bool(what_parts)
        if where_text is None and (len(segs) >= 2 or (len(segs) == 1 and what_in_who)):
            where_text = segs.pop()
        if where_text is None and len(segs) == 1 and self.ref.find_place(segs[0]):
            where_text = segs.pop()
        what_parts += segs

        if cc:
            code = cc.lower() if len(cc) == 2 else cc_for_name(cc)
            result.ccs.append((code or cc.lower(), cc))
        if where_text:
            place = self.ref.find_place(where_text)
            if place:
                result.places.append(place)
            else:
                # Unknown place: keep the whole text, but still read countries out of it.
                tokens = self._tokens(where_text)
                self._classify(tokens, result)
                if not result.places:
                    result.unknown_place = "-".join(_tidy_word(t) for t in tokens)
        what_text = "-".join(p for p in what_parts if p)
        masked = self._spans(what_text, result)
        tokens = self._tokens(masked)
        # Inside the WHAT, place and country words are part of the title (Bangladesh-Slides).
        rest = self._classify(tokens, result, allow_places=False, allow_countries=False)
        result.what_words = self._words(rest, result)

    # ---------------------------------------------------------- free-form
    def _parse_free(self, result: ParsedName, text: str) -> None:
        masked = self._spans(text, result)
        tokens = self._tokens(masked)
        # A two-letter country code is only trusted as the last token (Berlin_de, Oslo_no).
        if tokens and len(tokens[-1]) == 2 and tokens[-1].isalpha():
            last = tokens[-1].lower()
            if is_valid_cctld(last) and self.ref.language(last) is None:
                result.ccs.append((last, tokens[-1]))
                tokens = tokens[:-1]
        rest = self._classify(tokens, result)
        result.what_words = self._words(rest, result)
