import pytest

from mam.tools.renamer.parse import NameParser, find_verse


@pytest.fixture
def parser(reference):
    return NameParser(reference)


@pytest.mark.parametrize(
    ("text", "code", "tag"),
    [
        ("SB 3.1.28.", "SB-3-1-28", "3.1.28"),
        ("S.B. 1.19.31(with", "SB-1-19-31", "1.19.31"),
        ("KKS.BG.14,6", "BG-14-6", "14.6"),
        ("BG 13.8-12. 2008", "BG-13-8-12", "13.8-12"),  # verse range
        ("SB-9-20-20-27", "SB-9-20-20-27", "9.20.20-27"),
        ("CC-Madhya-20-154", "CC-Madhya-20-154", "Madhya 20.154"),
        ("KKS-Adi-4-142-05-9-11", "CC-Adi-4-142", "Adi 4.142"),  # lila without CC; date after
        ("SB 2.9.5 08-04-13", "SB-2-9-5", "2.9.5"),  # a spaced number is not a range
        ("CC-Course", "CC", ""),
    ],
)
def test_verses(text, code, tag):
    verse = find_verse(text)
    assert verse.code == code
    assert (verse.tag if verse.numbers else "") == tag


def test_book_mid_title_is_not_a_verse():
    assert find_verse("Sai-Baba-Program-BG-Talk") is None


@pytest.mark.parametrize(
    ("name", "what", "place", "ccs", "unknown"),
    [
        (
            "2011-08-20_KKS_Jaya-radha-madhava_oslo_fi",
            ["Jaya", "radha", "madhava"],
            "Oslo",
            ["fi"],
            None,
        ),
        (
            "2012-07-10_KKS-SB-6-1-15_Serbia-Summercamp_sr",
            [],
            None,
            ["sr", "rs"],
            "Serbia-Summercamp",
        ),
        ("2015-03-06_KKS-SB-3-1-1_NRK-swe", [], None, ["se"], "NRK"),
        (
            "2012-01-05_KKS_Bangladesh-Slides_Leipzig_de",
            ["Bangladesh", "Slides"],
            None,
            ["de"],
            "Leipzig",
        ),
        (
            "KKS_Janmastami evening lecture_03-09-2018_Slovenia",
            ["Janmastami", "evening", "lecture"],
            None,
            ["si"],
            None,
        ),
        (
            "A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD",
            ["Nezkracena"],
            "Farma-Krishna-Dvur",
            [],
            None,
        ),
        (
            "2020-03-30_KKS_Reading-from-Kirtaniyah_livestream",
            ["Reading", "from", "Kirtaniyah"],
            "Zoom-livestream",
            [],
            None,
        ),
        ("2012-01-20_KKS_CC-Course-V_Radhadesh_be", ["Course", "V"], "Radhadesh", ["be"], None),
    ],
)
def test_names(parser, name, what, place, ccs, unknown):
    parsed = parser.parse(name)
    assert parsed.what_words == what
    assert (parsed.places[0].name if parsed.places else None) == place
    assert [cc for cc, _ in parsed.ccs] == ccs
    assert parsed.unknown_place == unknown


@pytest.mark.parametrize("name", ["R09_0004", "ZOOM0004", "STE-000", "00008"])
def test_recorder_names(parser, name):
    parsed = parser.parse(name)
    assert parsed.recorder and not parsed.descriptive


def test_speakers_and_languages(parser):
    parsed = parser.parse("2012-07-12_SNS_Maha-Mantra_Serbia-Summercamp_rs")
    assert parsed.speakers == ["SNS"] and not parsed.has_kks
    parsed = parser.parse("2012-01-06_KKS_SB-3-23-33GER_Leipzig_de")
    assert parsed.has_kks and parsed.languages == ["German"] and parsed.verse.code == "SB-3-23-33"


def test_tascam_name_keeps_date_and_drops_index(parser):
    parsed = parser.parse("150904_0121_KADAMBA S.- KIRTAN-D")
    assert parsed.has_kks and parsed.recorder
    assert parsed.what_words == ["Kirtan"]
    assert parsed.dates[0].raw == "150904"


def test_glued_words_are_split(parser):
    assert parser.parse("KKS-RadhastamiBhajanas_05.09.11_Berlin").what_words == [
        "Radhastami",
        "Bhajanas",
    ]
    assert parser.parse("KKS_RadhaMadhava_05-09-11_Berlin").categories == ["Jaya-Radha-Madhava"]
