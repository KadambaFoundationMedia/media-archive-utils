import pytest

from mam.helpers.transcriber import Segment, Transcript
from mam.tools.class_discoverer.announce import best_announcement, find_announcements, read_window
from mam.tools.class_discoverer.numbers import parse_number, tokenize


def num(text):
    got = parse_number(tokenize(text), 0)
    return got[0] if got else None


@pytest.mark.parametrize(
    "text,value",
    [
        ("fifty-one", 51), ("fifty one", 51), ("thirty one", 31), ("two", 2), ("nineteen", 19),
        ("twenty", 20), ("one hundred and fifty four", 154), ("hundred", None), ("154", 154),
        ("first", 1), ("twenty first", 21), ("nine", 9), ("ninety", 90), ("hello", None),
    ],
)  # fmt: skip
def test_spoken_numbers(text, value):
    assert num(text) == value


def tr(*texts):
    return Transcript(
        [Segment(i * 10.0, i * 10.0 + 9, t) for i, t in enumerate(texts)],
        "en", "test", None, "test", None, "cpu",
    )  # fmt: skip


@pytest.mark.parametrize(
    "text,what",
    [
        ("We are reading from Bhagavad-gita as it is, chapter two, text fifty-one", "BG-2-51"),
        ("Bhagavad-gita as it is, Chapter 2, Text 51.", "BG-2-51"),
        ("Srimad-Bhagavatam, first canto, chapter nineteen, text thirty-one", "SB-1-19-31"),
        ("Reading from Srimad Bhagavatam canto 9 chapter 20 text 20 to 27", "SB-9-20-20-27"),
        ("Caitanya-caritamrta, Madhya lila, chapter twenty, text one hundred fifty-four",
         "CC-Madhya-20-154"),
        ("Srimad Bhagavatam 3.6.6", "SB-3-6-6"),
    ],
)  # fmt: skip
def test_read_window(text, what):
    book = "BG" if "gita" in text.lower() else "CC" if "ait" in text else "SB"
    ref, _, issue = read_window(book, text)
    assert ref is not None, issue
    assert ref.what == what


def test_incomplete_announcement_is_not_a_verse():
    ref, heard, issue = read_window("SB", "It's thirty one, first canto")
    assert ref is None and issue and heard["canto"] == 1


def test_best_announcement_takes_most_repeated():
    t = tr(
        "Bhagavad-gita as it is chapter two text fifty one",
        "um",
        "so in Bhagavad-gita chapter two text 51 Krishna says",
    )
    found = find_announcements(t)
    best, votes = best_announcement(found)
    assert best.ref.what == "BG-2-51" and votes["BG-2-51"] >= 2
