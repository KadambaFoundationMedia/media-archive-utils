"""Find dates in file and folder names.

Every reading of an ambiguous date is kept as a candidate (e.g. 05-09-11 can be 5 Sep 2011,
9 May 2011 or 11 Sep 2005); the resolver picks one using folder, sibling, travel-schedule and
media-table evidence, or flags the file for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MIN_YEAR = 1990
# The archive mostly uses European day-month-year; American month-day-year is only chosen
# when other evidence (folder, siblings, tags, travel schedule) points to it.
MDY_PRIOR = 0.15

MONTHS: dict[str, int] = {}
_MONTH_NAMES = {
    1: "jan january januari janvier enero januar leden ledna januar januara sijecanj sijecnja "
    "tammikuu",
    2: "feb february februari fevrier febrero februar unor unora februara veljaca veljace helmikuu",
    3: "mar march maart mars marzo marz brezen brezna marca ozujak ozujka maaliskuu",
    4: "apr april avril abril duben dubna aprila travanj travnja huhtikuu",
    5: "may mei mai mayo maj kveten kvetna maja svibanj svibnja toukokuu",
    6: "jun june juni juin junio cerven cervna junij junija lipanj lipnja kesakuu",
    7: "jul july juli juillet julio cervenec cervence julij julija srpanj srpnja heinakuu",
    8: "aug august augustus aout agosto srpen srpna avgust avgusta kolovoz kolovoza elokuu",
    9: "sep sept september septembre septiembre zari septembra rujan rujna syyskuu",
    10: "oct okt october oktober octobre octubre rijen rijna oktobra listopad listopada lokakuu",
    11: "nov november novembre noviembre listopad listopadu novembra studeni studenog marraskuu",
    12: "dec dez december dezember decembre diciembre prosinec prosince decembra prosinac prosinca "
    "joulukuu",
}
for _num, _names in _MONTH_NAMES.items():
    for _name in _names.split():
        MONTHS.setdefault(_name, _num)
# 'listopad' is October in Czech but November in Croatian; the Czech meaning is more common here.
MONTHS["listopad"] = 10
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

_ACCENTS = str.maketrans("áčďéěíňóřšťúůýžäöüåćđš", "acdeeinorstuuyzaouacds")


def fold(text: str) -> str:
    return text.lower().translate(_ACCENTS)


@dataclass(frozen=True)
class DateValue:
    year: int | None = None
    month: int | None = None
    day: int | None = None

    @property
    def is_full(self) -> bool:
        return None not in (self.year, self.month, self.day)

    @property
    def is_empty(self) -> bool:
        return self.year is None and self.month is None and self.day is None

    def text(self) -> str:
        y = f"{self.year:04d}" if self.year else "YYYY"
        mo = f"{self.month:02d}" if self.month else "MM"
        d = f"{self.day:02d}" if self.day else "DD"
        return f"{y}-{mo}-{d}"

    def as_date(self) -> date | None:
        return date(self.year, self.month, self.day) if self.is_full else None

    def agrees_with(self, other: DateValue) -> bool:
        """True when no known part contradicts the other value."""
        return all(
            a is None or b is None or a == b
            for a, b in ((self.year, other.year), (self.month, other.month), (self.day, other.day))
        )


@dataclass
class DateReading:
    value: DateValue
    fmt: str  # ymd | dmy | mdy | yymmdd | ddmmyy | month-year | year | ...
    prior: float  # how likely this reading is without context


@dataclass
class DateMatch:
    raw: str
    start: int
    end: int
    readings: list[DateReading] = field(default_factory=list)
    note: str | None = None

    @property
    def ambiguous(self) -> bool:
        full = {r.value for r in self.readings}
        return len(full) > 1


def _year(text: str) -> int | None:
    n = int(text)
    if len(text) == 4:
        return n
    if len(text) == 2:
        current = date.today().year % 100
        return 2000 + n if n <= current else 1900 + n
    return None


def _valid(y: int | None, m: int | None, d: int | None) -> bool:
    today = date.today()
    if y is not None and not MIN_YEAR <= y <= today.year:
        return False
    if m is not None and not 1 <= m <= 12:
        return False
    if d is not None and not 1 <= d <= 31:
        return False
    if None not in (y, m, d):
        try:
            return date(y, m, d) <= today
        except ValueError:
            return False
    return True


def _reading(fmt: str, prior: float, y, m, d) -> DateReading | None:
    if not _valid(y, m, d):
        return None
    return DateReading(DateValue(y, m, d), fmt, prior)


SEP = r"[-_. ]"
PATTERNS: list[tuple[str, str]] = [
    # 2012-07-05, 2008_04_09, 2012-01-XX, 2015-MM-DD
    ("iso", rf"(?<!\d)((?:19|20)\d{{2}}){SEP}(\d{{1,2}}|XX|MM){SEP}(\d{{1,2}}|XX|DD)(?!\d)"),
    # 20120705
    ("compact8", r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)"),
    # 6-13-jul-2012, 28-31.July.2012 (a stay; the day is unknown)
    (
        "range-month-y",
        rf"(?<!\d)(\d{{1,2}})-(\d{{1,2}}){SEP}({MONTH_RE})\.?{SEP}*((?:19|20)\d{{2}})(?!\d)",
    ),
    # 08-mar-2015, 8 March 2015
    ("d-month-y", rf"(?<![\d])(\d{{1,2}}){SEP}?({MONTH_RE})\.?{SEP}*((?:19|20)\d{{2}})(?!\d)"),
    # July 2012, okt 2008, DUBEN 2008, Aug-2009, Jan. 2012
    ("month-y", rf"(?<![a-z])({MONTH_RE})\.?{SEP}*((?:19|20)\d{{2}})(?!\d)"),
    # 29-8-2011, 05.09.2011
    ("dmy4", rf"(?<![a-z\d])(\d{{1,2}}){SEP}(\d{{1,2}}){SEP}((?:19|20)\d{{2}})(?!\d)"),
    # 29-8-11, 05.09.11, 27_8_15, 03-10-23
    ("short3", rf"(?<![a-z\d])(\d{{1,2}}){SEP}(\d{{1,2}}){SEP}(\d{{2}})(?![\d])"),
    # 150904, 081003
    ("compact6", r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)"),
    # 2006
    ("year", r"(?<![\d])((?:19|20)\d{2})(?![\d])"),
]


def find_dates(text: str, masked: list[tuple[int, int]] | None = None) -> list[DateMatch]:
    """All date-like matches in `text`, skipping the `masked` spans (e.g. verse numbers)."""
    folded = fold(text)
    taken: list[tuple[int, int]] = list(masked or [])
    matches: list[DateMatch] = []

    def free(start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in taken)

    for kind, pattern in PATTERNS:
        regex = re.compile(pattern, re.IGNORECASE)
        pos = 0
        while (match := regex.search(folded, pos)) is not None:
            start, end = match.span()
            pos = start + 1  # overlapping candidates must still be tried
            if not free(start, end):
                continue
            readings = _readings(kind, match.groups())
            if not readings:
                continue
            note = "day range in name; day unknown" if kind == "range-month-y" else None
            matches.append(DateMatch(text[start:end], start, end, readings, note))
            taken.append((start, end))
            pos = end
    return sorted(matches, key=lambda m: m.start)


def _readings(kind: str, g: tuple[str, ...]) -> list[DateReading]:
    out: list[DateReading | None] = []
    if kind == "iso":
        y = int(g[0])
        m = None if g[1].upper() in ("XX", "MM") else int(g[1])
        d = None if g[2].upper() in ("XX", "DD") else int(g[2])
        out.append(_reading("ymd", 1.0, y, m, d))
    elif kind == "compact8":
        out.append(_reading("ymd", 0.9, int(g[0]), int(g[1]), int(g[2])))
    elif kind == "d-month-y":
        out.append(_reading("d-month-y", 1.0, int(g[2]), MONTHS[g[1].lower()], int(g[0])))
    elif kind == "range-month-y":
        out.append(_reading("month-y", 0.9, int(g[3]), MONTHS[g[2].lower()], None))
    elif kind == "month-y":
        out.append(_reading("month-y", 0.9, int(g[1]), MONTHS[g[0].lower()], None))
    elif kind == "dmy4":
        a, b, y = int(g[0]), int(g[1]), int(g[2])
        out += [_reading("dmy", 0.6, y, b, a), _reading("mdy", MDY_PRIOR, y, a, b)]
    elif kind == "short3":
        a, b, c = g
        y = _year(c)
        out += [
            _reading("dmy", 0.6, y, int(b), int(a)),
            _reading("mdy", MDY_PRIOR, y, int(a), int(b)),
        ]
        if len(a) == 2 and len(b) == 2:  # yy-mm-dd only with zero-padded parts
            out.append(_reading("ymd", 0.3, _year(a), int(b), int(c)))
    elif kind == "compact6":
        a, b, c = g
        out += [
            _reading("yymmdd", 0.6, _year(a), int(b), int(c)),
            _reading("ddmmyy", 0.4, _year(c), int(b), int(a)),
        ]
    elif kind == "year":
        out.append(_reading("year", 0.5, int(g[0]), None, None))
    readings = [r for r in out if r is not None]
    # Collapse readings that give the same date (e.g. 05-05-11 as dmy and mdy).
    unique: dict[DateValue, DateReading] = {}
    for r in readings:
        if r.value not in unique or unique[r.value].prior < r.prior:
            unique[r.value] = r
    return list(unique.values())
