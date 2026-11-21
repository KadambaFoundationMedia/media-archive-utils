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


def real_segments(*lines):
    """(start seconds, text) lines copied from a real transcript."""
    return Transcript(
        [Segment(t, t + 4, text) for t, text in lines],
        "en", "test", None, "test", None, "cpu",
    )  # fmt: skip


def test_a_verse_quoted_in_the_purport_is_not_the_class_verse():
    """2008-01-04-2.mp3 on the Mac: the class verse (BG 2.51) was not announced in words, its
    translation was read after 'Translation', and the purport quotes 'Bhagavatam 10.14.58 says'."""
    t = real_segments(
        (1745, "Paranga Chantyana Amajan Kayaan"),
        (1780, "Translation"),
        (1782, "By thus engaging in devotional service to the Lord,"),
        (1809, "Bhagavatam 10.14.58 says,"),
        (1812, "Samasri-tāya-pade-palāva-pāvam, ahat-pādham-punyūvya-sau-murga-ave,"),
    )
    found = find_announcements(t)
    assert found and all(a.quoted for a in found if a.ref)
    best, _ = best_announcement(found)
    assert best is None


def test_a_mention_late_in_the_recording_is_not_the_class_verse():
    t = real_segments((1500, "Krishna tells in Srimad Bhagavatam canto 3 chapter 6 text 6"))
    assert best_announcement(find_announcements(t))[0] is None


def test_announcement_before_the_translation_still_counts():
    t = real_segments(
        (5, "Reading from Bhagavad-gita as it is, chapter two, text fifty-one"),
        (30, "Translation"),
        (31, "By thus engaging in devotional service to the Lord,"),
        (90, "Bhagavatam 10.14.58 says,"),
    )
    best, _ = best_announcement(find_announcements(t))
    assert best.ref.what == "BG-2-51"
