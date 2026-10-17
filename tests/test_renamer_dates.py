import pytest

from mam.tools.renamer.dates import DateValue, find_dates


def readings(text, masked=None):
    return [[r.value.text() for r in m.readings] for m in find_dates(text, masked)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2012-07-05_KKS", [["2012-07-05"]]),
        ("2012-01-XX_KKS", [["2012-01-DD"]]),
        ("HH KKS BG 7.17 2008-04-12", [["2008-04-12"]]),
        ("2008_04_09", [["2008-04-09"]]),
        ("27_8_15", [["2015-08-27"]]),  # only day-month-year is valid
        ("08-mar-2015", [["2015-03-08"]]),
        ("Prague-Oct-2003", [["2003-10-DD"]]),
        ("KKS DUBEN 2008 MP3", [["2008-04-DD"]]),  # Czech month name
        ("Serbia-summercamp-6-13-jul-2012", [["2012-07-DD"]]),  # a stay, day unknown
        ("vyasa puja 2015", [["2015-MM-DD"]]),
        ("2015.01.25 10.12.54", [["2015-01-25"]]),
    ],
)
def test_single_reading(text, expected):
    assert readings(text) == expected


def test_ambiguous_dates_keep_every_reading():
    assert readings("05-09-11") == [["2011-09-05", "2011-05-09", "2005-09-11"]]
    assert readings("150904") == [["2015-09-04", "2004-09-15"]]  # TASCAM yymmdd first


def test_masked_verse_is_not_a_date():
    # 'SB 2.9.8' is masked; the date after it must still be found (overlap regression).
    assert readings("HH KKS SB 2.9.8 08-04-13", masked=[(7, 15)])[0][2] == "2008-04-13"


def test_digits_glued_to_letters_are_not_dates():
    assert readings("RadhastamiBahjanas2_05.09.11")[0][0] == "2011-09-05"


def test_future_and_ancient_dates_are_rejected():
    assert readings("01-01-85") == []  # 1985 is before the archive (1990+)
    assert DateValue(2012, 7, None).text() == "2012-07-DD"
