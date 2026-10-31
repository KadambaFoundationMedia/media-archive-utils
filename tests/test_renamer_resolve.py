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
        "KKS DUBEN 2008 MP3/03 BRNO LEKCE STEREO JET.mp3",
        "KKS DUBEN 2008 MP3/07 KKS PRUHON.mp3",
        "two-countries/KKS SB 1.1.1.mp3",
        "two-countries/KKS Brno.mp3",
        "two-countries/KKS Oslo.mp3",
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
        # DUBEN is April in Czech; the country comes from 'BRNO LEKCE' in the same folder
        ("KKS DUBEN 2008 MP3/06 KKS SB 3.1.25.mp3", "2008-04-DD_KKS_SB-3-1-25_cz.mp3"),
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
    assert any("place_location option in Baserow" in f for f in p.flags)
    assert not any("travel schedule" in f.lower() for f in p.flags if f.startswith("New place"))


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


def test_ambiguous_numbers_default_to_european_order(propose):
    # 03-04-11 with no other evidence: day-month-year, the usual order in the archive
    p = propose("ambiguous/KKS_Lecture_03-04-11.mp3")
    assert p.when == "2011-04-03" and not p.flags


def test_baserow_row_is_matched_by_verse(propose):
    p = propose("HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3")
    assert p.baserow_row_id == 2335


def test_original_name_is_unchanged(propose, tree):
    path = tree / "UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3"
    propose("UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3")
    assert path.exists() and Path(path).name == "HH KKS BG 7.17 2008-04-12.mp3"


def tagged(settings, folder, name, **tags):
    from tests.conftest import make_tone

    path = settings.media_sample_files_path / folder / name
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    make_tone(path, seconds=1, codec_args=["-c:a", "libmp3lame", *meta])
    return path


def test_id3_title_date_is_trusted(reference, settings):
    """The real case: 'HH KKS BG 2.54.mp3' has title 'HH KKS BG2.54 11.07.2008'."""
    path = tagged(settings, "tagged", "HH KKS BG 2.54.mp3", title="HH KKS BG2.54 11.07.2008")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert p.when == "2008-07-11" and not p.flags  # European order, from the ID3 title
    origins = [e.detail["origin"] for e in p.evidence if e.field == "when" and e.detail]
    assert any('ID3 tags: title "HH KKS BG2.54 11.07.2008"' in o for o in origins)


def test_id3_date_tag_beats_a_partial_folder_date(reference, settings):
    path = tagged(settings, "Prague-Oct-2003", "Kirtan 2.mp3", date="2003-10-24")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert p.when == "2003-10-24"


def test_id3_date_that_conflicts_with_the_folder_is_flagged(reference, settings):
    path = tagged(settings, "Prague-Oct-2003", "Kirtan 3.mp3", title="Kirtan 24.10.2009")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert any("conflicts with the folder name" in f for f in p.flags)


def test_id3_tags_confirm_an_ambiguous_filename_date(reference, settings):
    # 05-04-11 alone reads as 5 April; the ID3 tag says 4 May (American order) -> 4 May
    path = tagged(settings, "x", "KKS_Lecture_05-04-11.mp3", date="2011-05-04")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert p.when == "2011-05-04"
    assert any("ID3 tags confirm" in n for n in p.notes)


def test_travel_day_is_shown_but_left_open(reference, settings, tree):
    from datetime import date

    from mam.tools.renamer.knowledge import Stay

    reference.stays += [
        Stay(date(2007, 12, 29), date(2008, 1, 2), "Simhachalam", "Germany", "de"),
        Stay(date(2008, 1, 2), date(2008, 1, 5), "Stockholm", "Sweden", "se"),
    ]
    path = tree / "2008" / "2008-01-02.mp3"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    travel = [e for e in p.evidence if e.source == "travel"]
    assert {e.value for e in travel} == {"Simhachalam", "Stockholm"}
    assert p.place is None
    assert any(
        "Travel day on 2008-01-02: KKS leaves Simhachalam and arrives in Stockholm" in n
        for n in p.notes
    )


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("UNCATEGORIZED/HH KKS BG 7.17 2008-04-12.mp3", "BG 7.17"),
        ("From JVD (8.9.11)/KKS-Adi-4-142-05-9-11_Berlin.WMA", "CC Adi 4.142"),
        ("KKS DUBEN 2008 MP3/06 KKS SB 3.1.25.mp3", "SB 3.1.25"),
    ],
)
def test_titles_write_verses_with_dots(propose, name, title):
    """Verse numbers: hyphens in filenames, dots in titles (docs/references/verse-structure.md)."""
    assert propose(name).title == title


@pytest.fixture
def media_rows(reference):
    from mam.tools.renamer.knowledge import MediaRow

    def add(*rows):
        for row in rows:
            reference.media_by_date.setdefault(row.date, []).append(row)

    return add, MediaRow


def test_baserow_row_needs_more_than_the_date(propose, media_rows):
    add, Row = media_rows
    # Same date, same place, but a different recording: not a match
    add(Row(901, "2011-08-28", "Kirtan", "Oslo", "Norway", "", "Sunday kirtan"))
    p = propose("From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA")
    assert p.baserow_row_id is None
    assert any("none clearly the same recording" in n for n in p.notes)


def test_baserow_row_matches_on_title_and_place(propose, media_rows):
    add, Row = media_rows
    add(
        Row(
            902, "2011-08-28", "Srimad-bhagavatam", "Oslo", "Norway", "", "SB 1.19.30 morning class"
        ),
        Row(903, "2011-08-28", "Kirtan", "Oslo", "Norway", "", "Sunday kirtan"),
    )
    p = propose("From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA")
    assert p.baserow_row_id == 902


def test_different_verse_or_place_counts_against_a_row(propose, media_rows):
    add, Row = media_rows
    add(Row(904, "2011-08-28", "Srimad-bhagavatam", "Berlin", "Germany", "1.19.31", "SB 1.19.31"))
    p = propose("From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA")
    assert p.baserow_row_id is None


def test_several_possible_rows_are_flagged(propose, media_rows):
    add, Row = media_rows
    add(
        Row(
            905, "2011-08-28", "Srimad-bhagavatam", "Oslo", "Norway", "1.19.30", "SB 1.19.30 part 1"
        ),
        Row(
            906, "2011-08-28", "Srimad-bhagavatam", "Oslo", "Norway", "1.19.30", "SB 1.19.30 part 2"
        ),
    )
    p = propose("From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .WMA")
    assert p.baserow_row_id is None
    assert any("Several Baserow rows may be this recording" in f for f in p.flags)


def test_livestream_place_is_not_a_conflict(reference, settings, tree, media_rows):
    add, Row = media_rows
    add(
        Row(
            475,
            "2020-03-30",
            "Book-discussions",
            "ISKCON-Durban",
            "South-Africa",
            "",
            "Reading From Kirtaniyah Sada Harih",
        )
    )
    path = tree / "2020-03-30_KKS_Reading-from-Kirtaniyah-sada-harih_livestream.mp4"
    path.write_bytes(b"")
    p = Resolver(reference, [settings.media_sample_files_path]).propose(path)
    assert p.baserow_row_id == 475


def test_country_from_places_named_by_other_files_in_the_folder(propose):
    proposal = propose("KKS DUBEN 2008 MP3/06 KKS SB 3.1.25.mp3")
    assert proposal.cc == "cz" and proposal.place is None  # the city stays open
    origins = [e.detail.get("origin") for e in proposal.evidence if e.source == "siblings"]
    assert origins == ["other files in this folder name Brno"]
    assert any("not proof" in n for n in proposal.notes)
    # places in two countries: no country is guessed
    assert propose("two-countries/KKS SB 1.1.1.mp3").cc is None


def test_alias_of_a_not_places_word_maps_to_it():
    from mam.tools.renamer.knowledge import build_reference, norm_key
    from tests.renamer_fixtures import reference_raw

    raw = reference_raw()
    raw["options"]["place_location"].append("Summercamp")
    raw["place_country"].append(
        {
            "id": 900,
            "place": "Summercamp",
            "country": "",
            "cc": "",
            "aliases": "Serbia-Summercamp;Fruska-Goranga",
        }
    )
    ref = build_reference(raw)
    assert ref.places[norm_key("Fruska-Goranga")].name == "Summercamp"
    assert ref.places[norm_key("Serbia-Summercamp")].name == "Summercamp"
    assert norm_key("Summercamp") not in ref.places
