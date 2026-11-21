"""A verse reference and how it maps to the WHAT, the Baserow tag and the vedabase.io URL.

See docs/references/verse-structure.md: BG chapter-verse, SB canto-chapter-verse, CC
lila-chapter-verse; any extra number is the end of a range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LENGTH = {"BG": 2, "SB": 3, "CC": 2}  # numbers of a single verse (CC: after the lila)
LILAS = ("Adi", "Madhya", "Antya")
WHAT_RE = re.compile(
    r"^(?P<book>BG|SB|CC)(?:-(?P<lila>Adi|Madhya|Antya))?(?P<nums>(?:-\d{1,3})+)(?=-|$)"
)


@dataclass(frozen=True)
class VerseRef:
    book: str  # BG | SB | CC
    numbers: tuple[int, ...]  # single verse, without the range end
    lila: str | None = None  # CC only
    range_end: int | None = None

    @classmethod
    def from_what(cls, what: str | None) -> VerseRef | None:
        """'SB-9-20-20-Dont-waste-time' -> SB 9.20.20; 'BG-2-51-53' -> BG 2.51-53."""
        match = WHAT_RE.match(what or "")
        if not match:
            return None
        book = match.group("book")
        nums = [int(n) for n in match.group("nums").split("-")[1:]]
        if book == "CC" and match.group("lila") is None:
            return None
        if len(nums) < LENGTH[book] or len(nums) > LENGTH[book] + 1:
            return None
        end = nums[LENGTH[book]] if len(nums) > LENGTH[book] else None
        single = tuple(nums[: LENGTH[book]])
        if end is not None and end <= single[-1]:
            return None  # not a range (verse-structure.md): flag instead
        return cls(book, single, match.group("lila"), end)

    @property
    def what(self) -> str:
        parts = [self.book] + ([self.lila] if self.lila else [])
        parts += [str(n) for n in self.numbers]
        if self.range_end is not None:
            parts.append(str(self.range_end))
        return "-".join(parts)

    @property
    def tag(self) -> str:
        """The Baserow `tag`: '2.51', '9.20.20-27', 'Madhya 20.154'."""
        nums = ".".join(str(n) for n in self.numbers)
        if self.range_end is not None:
            nums += f"-{self.range_end}"
        return f"{self.lila} {nums}" if self.lila else nums

    @property
    def readable(self) -> str:
        return f"{self.book} {self.tag}"

    def verses(self) -> list[VerseRef]:
        """The single verses of a range (or just this verse)."""
        if self.range_end is None:
            return [self]
        head = self.numbers[:-1]
        return [
            VerseRef(self.book, (*head, n), self.lila)
            for n in range(self.numbers[-1], self.range_end + 1)
        ]

    @property
    def path(self) -> str:
        """Path of the single verse's page: /en/library/cc/madhya/20/154/"""
        parts = [self.book.lower()] + ([self.lila.lower()] if self.lila else [])
        parts += [str(n) for n in self.numbers]
        return "/en/library/" + "/".join(parts) + "/"

    def same_verse_as(self, other: VerseRef) -> bool:
        return (self.book, self.lila, self.numbers) == (other.book, other.lila, other.numbers)
