"""ID3 tag writing by the Renamer (docs/references/idv3-tags.md)."""

import shutil

import pytest
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from mam.errors import MamError
from mam.helpers.file_organizer import CategoryMatcher
from mam.tools.renamer.tags import apply_tags, build_tags, write_tags
from tests.conftest import make_tone
from tests.test_manager import archive, client  # noqa: F401


def proposal(**kw):
    base = {
        "what": "SB-9-20-20-27-The-King-Refused-to-Accept",
        "when": "2013-11-14",
        "place": "Vrindavan",
        "cc": "in",
        "category": "Srimad-bhagavatam",
    }
    return {**base, **kw}


def test_the_example_of_the_reference_file():
    tags = build_tags(proposal(), "Srimad Bhagavatam", "India")
    assert tags == {
        "title": "SB 9.20.20-27 - The King Refused to Accept",
        "artist": "Kadamba Kanana Swami",
        "album": "SB Canto 9",
        "genre": "Srimad Bhagavatam",
        "album_artist": "Kadamba Kanana Swami",
        "composer": "Kadamba Kanana Swami",
        "year": "2013",
        "comment": "2013-11-14, Vrindavan, India",
        "publisher": "Kadamba Foundation",
        "copyright": "Kadamba Foundation",
    }


def test_language_is_the_two_letter_code():
    from mam.tools.renamer.tags import language_codes

    assert language_codes(["English"]) == "en"
    assert language_codes(["English", "Dutch", "Klingon"]) == "en,nl"
    assert build_tags(proposal(), None, None, "en")["language"] == "en"


def test_album_is_the_canto_not_the_chapter():
    assert build_tags(proposal(what="SB-1-9-31"), None, None)["album"] == "SB Canto 1"


def test_title_is_the_verse_alone_until_there_is_a_title():
    assert build_tags(proposal(what="SB-9-20-20"), None, None)["title"] == "SB 9.20.20"


def test_other_books_group_by_chapter_or_lila():
    assert build_tags(proposal(what="BG-17-3"), None, None)["album"] == "BG chapter 17"
    cc = build_tags(proposal(what="CC-Madhya-20-154"), None, None)
    assert cc["album"] == "CC Madhya-lila" and cc["title"] == "CC Madhya 20.154"


def test_no_verse_gives_category_and_year_and_the_what_as_title():
    tags = build_tags(
        proposal(what="Janmastami-evening-lecture", category="Janmastami", when="2019-08-23"),
        None,
        None,
    )
    assert tags["album"] == "Janmastami - 2019"
    assert tags["title"] == "Janmastami evening lecture"
    assert tags["genre"] == "Janmastami"  # the category when idv3_genre is empty


def test_unknown_parts_are_left_out():
    tags = build_tags(proposal(when="2015-MM-DD", place=None, cc=None, what=None), None, None)
    assert tags["comment"] == "2015" and "title" not in tags and tags["year"] == "2015"


def test_genre_comes_from_the_idv3_genre_column(category_rows):
    rows = [
        {**r, "idv3_genre": "Srimad Bhagavatam"} if r["category"] == "Srimad-bhagavatam" else r
        for r in category_rows
    ]
    cat = CategoryMatcher(rows).by_name("Srimad-bhagavatam")
    assert cat.genre == "Srimad Bhagavatam"


@pytest.fixture
def copy(tone, settings):
    """A processed copy: the tagged file is never an original."""
    dst = settings.media_processed_files_path / "copy.mp3"
    shutil.copy(tone, dst)
    return dst


def test_mp3_tags_are_overwritten_and_other_frames_kept(copy):
    from mutagen.id3 import APIC, COMM, ID3, TALB, TRCK

    audio = ID3()
    audio.add(TALB(encoding=3, text=["ZOOM"]))
    audio.add(TRCK(encoding=3, text=["7"]))
    audio.add(COMM(encoding=3, lang="eng", desc="", text=["old"]))
    audio.add(APIC(encoding=3, mime="image/png", type=3, desc="c", data=b"x"))
    audio.save(copy)
    write_tags(copy, build_tags(proposal(), "Srimad Bhagavatam", "India"))
    out = ID3(copy)
    assert str(out["TALB"]) == "SB Canto 9" and str(out["TPE1"]) == "Kadamba Kanana Swami"
    assert str(out["TCON"]) == "Srimad Bhagavatam" and str(out["TDRC"]) == "2013"
    assert out.getall("COMM")[0].text == ["2013-11-14, Vrindavan, India"]
    assert "TRCK" not in out and out.getall("APIC")
    assert str(out["TPUB"]) == str(out["TCOP"]) == "Kadamba Foundation"


def test_m4a_tags(settings):
    path = make_tone(
        settings.media_processed_files_path / "t.m4a", codec_args=["-c:a", "aac", "-b:a", "64k"]
    )
    write_tags(path, build_tags(proposal(), "Srimad Bhagavatam", "India"))
    tags = MP4(path).tags
    assert tags["\xa9alb"] == ["SB Canto 9"] and tags["\xa9day"] == ["2013"]
    assert tags["cprt"] == ["Kadamba Foundation"]


def test_video_is_not_tagged(settings):
    with pytest.raises(MamError):
        write_tags(settings.media_sample_files_path / "x.mp4", {})
    out = apply_tags(
        settings.media_sample_files_path / "x.mp4",
        proposal(),
        None,
        dry_run=False,
        media_file_id=None,
        tool="t",
    )
    assert not out["written"] and out["tags"] == {}


def test_originals_are_never_written(tone, settings):
    with pytest.raises(Exception, match="originals"):
        write_tags(tone, {"title": "x"})


def test_a_renamer_run_tags_the_copy_and_a_dry_run_does_not(
    client, archive, reference, no_baserow, settings
):
    from sqlalchemy import select

    from mam.helpers.local_db import models as m
    from mam.helpers.local_db import session_scope
    from tests.test_manager import ids, run_queue

    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "dry_run": "on"})
    run_queue()
    assert not any(settings.media_processed_files_path.rglob("*.mp3"))
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    copy = next(settings.media_processed_files_path.rglob("*.mp3"))
    tags = ID3(copy)
    assert str(tags["TPE1"]) == "Kadamba Kanana Swami"
    assert str(tags["TIT2"]).startswith("SB 1.19.30") and str(tags["TALB"]) == "SB Canto 1"
    assert str(tags["TDRC"]) == "2011"
    with session_scope() as s:
        events = s.scalars(select(m.FileEvent).where(m.FileEvent.action == "write_tags")).all()
        assert len(events) == 1 and events[0].detail["written"] is True
