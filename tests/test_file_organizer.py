import shutil

import pytest

from mam.errors import ReadOnlyViolation
from mam.helpers.file_organizer import UNRESOLVED, CategoryMatcher, FileOrganizer
from mam.helpers.local_db.hashing import sha256


@pytest.fixture
def matcher(category_rows):
    return CategoryMatcher(category_rows)


@pytest.mark.parametrize(
    ("what", "category"),
    [
        ("SB-9-20-20", "Srimad-bhagavatam"),
        ("S.B.-3-1-28", "Srimad-bhagavatam"),
        ("BG-2-51", "Bhagavad-gita"),
        ("CC-Madhya-20-154", "CC-Madhya-lila"),
        ("CC-Raghunatha-das-goswami", "Caitanya-caritamrta"),
        ("Jaya-radha-madhava", "Jaya-Radha-Madhava"),
        ("Kirtan", "Kirtan"),
        ("SB-10-3-11-Janmastami", "Srimad-bhagavatam"),
        ("Janmastami-evening-lecture", "Janmastami"),
    ],
)
def test_category_matching(matcher, what, category):
    assert matcher.match(what).category.name == category


@pytest.mark.parametrize("what", [None, "", "ZOOM0004", "class", "Unknown"])
def test_no_category(matcher, what):
    assert matcher.match(what) is None


def test_original_is_copied_and_left_untouched(matcher, tone, settings):
    before = sha256(tone)
    org = FileOrganizer(matcher)
    dry = org.organize(tone, "2011-08-20_KKS_SB-1-19-31_fi.mp3", "SB-1-19-31", tool="t")
    assert dry.action == "copy" and not dry.dst.exists()

    result = org.organize(
        tone, "2011-08-20_KKS_SB-1-19-31_fi.mp3", "SB-1-19-31", tool="t", dry_run=False
    )
    assert result.dst == settings.media_processed_files_path / "Srimad-bhagavatam" / result.dst.name
    assert result.dst.exists() and tone.exists() and sha256(tone) == before
    assert not list(result.dst.parent.glob(".*.part"))


def test_missing_what_goes_to_unresolved(matcher, tone, settings):
    result = FileOrganizer(matcher).organize(
        tone, "2008-01-04_KKS.mp3", None, tool="t", dry_run=False
    )
    assert result.dst.parent == settings.media_processed_files_path / UNRESOLVED


def test_same_name_same_size_is_duplicate(matcher, tone, settings):
    org = FileOrganizer(matcher)
    first = org.organize(tone, "a_KKS_BG-2-51.mp3", "BG-2-51", tool="t", dry_run=False)
    twin = settings.media_sample_files_path / "twin.mp3"
    shutil.copy2(tone, twin)
    second = org.organize(twin, "a_KKS_BG-2-51.mp3", "BG-2-51", tool="t", dry_run=False)
    assert second.duplicate and not second.needs_review
    assert second.dst.name == "a_KKS_BG-2-51_duplicate.mp3" and first.dst.exists()


def test_same_name_different_file_gets_suffix_and_review(matcher, tone, settings):
    org = FileOrganizer(matcher)
    org.organize(tone, "a_KKS_BG-2-51.mp3", "BG-2-51", tool="t", dry_run=False)
    other = settings.media_sample_files_path / "other.mp3"
    other.write_bytes(b"different content")
    result = org.organize(other, "a_KKS_BG-2-51.mp3", "BG-2-51", tool="t", dry_run=False)
    assert result.dst.name == "a_KKS_BG-2-51-2.mp3" and result.needs_review


def test_processed_copy_is_moved_when_what_is_found(matcher, tone, settings):
    org = FileOrganizer(matcher)
    unresolved = org.organize(tone, "x_KKS.mp3", None, tool="t", dry_run=False)
    moved = org.organize(unresolved.dst, "x_KKS_BG-2-51.mp3", "BG-2-51", tool="t", dry_run=False)
    assert moved.action == "move"
    assert moved.dst.exists() and not unresolved.dst.exists() and tone.exists()


def test_never_writes_into_originals(category_rows, tone, settings, monkeypatch):
    monkeypatch.setattr(settings, "media_processed_files_path", settings.media_sample_files_path)
    with pytest.raises(ReadOnlyViolation):
        FileOrganizer(CategoryMatcher(category_rows), settings).organize(
            tone, "y.mp3", "BG-2-51", tool="t", dry_run=False
        )
