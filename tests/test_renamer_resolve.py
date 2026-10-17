from pathlib import Path

import pytest

from mam.tools.renamer.resolve import Resolver


@pytest.fixture
def tree(settings):
    """A small archive copied from the real sample names (empty files are enough to parse)."""
    root = settings.media_sample_files_path
    names = [
        "From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA",
        "From JVD (8.9.11)/KKS-Adi-4-142-05-9-11_Berlin.WMA",
        "From JVD (8.9.11)/KKS_Nrsimha Prayers_05.09.11_berlin.WMA",
        "UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3",
        "UNCATEGORIZED/HH KKS SB 2.9.7. 2008-04-12.mp3",
        "UNCATEGORIZED/HH KKS evening program 08-04-10.mp3",
        "Prague-Oct-2003/Lekce/A019 03-10-23 BG 3.12 Praha.mp3",
        "Prague-Oct-2003/Kirtany/JRM.mp3",
        "KKS DUBEN 2008 MP3/06 KKS SB 3.1.25.mp3",
        "Serbia-summercamp-6-13-jul-2012/R09_0004.MP3",
        "Serbia-summercamp-6-13-jul-2012/2012-07-12_SNS_Maha-Mantra_Serbia-Summercamp_rs.mp3",
        "2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3",
        "HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3",
        "JANMASTAMI  LJUBLANA 2015/150905_0124_D.mp3",
        "Oslo/2011-08-28_KKS_Bhajans_Oslo_sr.mp3",
        "ambiguous/KKS_Lecture_03-04-11.mp3",
    ]
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return root


@pytest.fixture
def propose(reference, settings, tree):
    resolver = Resolver(reference, [p for _, p in settings.import_roots])
    return lambda name: resolver.propose(tree / name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA",
            "2011-08-28_KKS_SB-1-19-30_Oslo-no.wma",
        ),
        # 05-9-11: day-month-year, confirmed by the travel schedule (Berlin, Sep 2011)
        (
            "From JVD (8.9.11)/KKS-Adi-4-142-05-9-11_Berlin.WMA",
            "2011-09-05_KKS_CC-Adi-4-142_Berlin-de.wma",
        ),
        ("UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3", "2008-04-12_KKS_BG-7-17.mp3"),
        # 08-04-10: year-month-day because the sibling files are from April 2008
        ("UNCATEGORIZED/HH KKS evening program 08-04-10.mp3", "2008-04-10_KKS_evening-program.mp3"),
        # 03-10-23 in a 2003 folder: year-month-day; Praha is Prague
        (
            "Prague-Oct-2003/Lekce/A019 03-10-23 BG 3.12 Praha.mp3",
            "2003-10-23_KKS_BG-3-12_Prague-cz.mp3",
        ),
        # no date or place in the name: both from the folder above
        ("Prague-Oct-2003/Kirtany/JRM.mp3", "2003-10-DD_KKS_JRM_Prague-cz.mp3"),
        ("KKS DUBEN 2008 MP3/06 KKS SB 3.1.25.mp3", "2008-04-DD_KKS_SB-3-1-25.mp3"),
        (
            "HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3",
            "2015-08-27_KKS_SB-3-6-6_se.mp3",
        ),
        ("JANMASTAMI  LJUBLANA 2015/150905_0124_D.mp3", "2015-09-05_KKS_Ljubljana-si.mp3"),
    ],
)
def test_names(propose, name, expected):
    assert propose(name).new_name == expected


def test_clean_names_are_not_flagged(propose):
    p = propose("From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA")
    assert p.flags == []
    assert p.category == "Srimad-bhagavatam" and p.verse_tag == "1.19.30"
    assert p.place_name == "Oslo" and p.cc == "no"


def test_recorder_file_is_flagged_but_still_named(propose):
    p = propose("Serbia-summercamp-6-13-jul-2012/R09_0004.MP3")
    assert p.new_name == "2012-07-DD_KKS_rs.mp3"
    assert any("Non-descriptive" in f for f in p.flags)


def test_other_speaker_is_flagged(propose):
    p = propose(
        "Serbia-summercamp-6-13-jul-2012/2012-07-12_SNS_Maha-Mantra_Serbia-Summercamp_rs.mp3"
    )
    assert any("Another speaker" in f for f in p.flags)
    assert any("New place 'Serbia-Summercamp'" in f for f in p.flags)


def test_new_place_suggests_closest_known_place(reference, settings, tree):
    path = tree / "2012-07-12_KKS_SB-1-1-3_Fruska-Goranga_rs.mp3"
    path.write_bytes(b"")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert any("did you mean Fruska-Gora" in f for f in p.flags)


def test_country_conflict_is_flagged(propose):
    p = propose("2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3")
    assert any("does not match Oslo" in f for f in p.flags)


def test_invalid_country_is_corrected_from_place(propose):
    p = propose("Oslo/2011-08-28_KKS_Bhajans_Oslo_sr.mp3")
    assert p.cc == "no" and not p.flags
    assert any("Corrected country code 'sr' to 'no'" in n for n in p.notes)


def test_ambiguous_date_is_flagged_with_known_parts(propose):
    p = propose("ambiguous/KKS_Lecture_03-04-11.mp3")
    assert p.when == "YYYY-04-DD"  # 2011-04-03 or 2003-04-11: only the month is certain
    assert any("Date format cannot be determined" in f for f in p.flags)


def test_baserow_row_is_matched_by_verse(propose):
    p = propose("HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3")
    assert p.baserow_row_id == 2335


def test_original_name_is_unchanged(propose, tree):
    path = tree / "UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3"
    propose("UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3")
    assert path.exists() and Path(path).name == "HH KKS BG 7.17 2008-04-12.mp3"
