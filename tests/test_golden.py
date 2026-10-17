"""Sanity checks on the golden fixtures, plus slow tests on real samples."""

from pathlib import Path

import pytest
import yaml

from mam.helpers.audio_optimizer import TARGET_I, TARGET_TP, normalize_whole, stream_cut
from tests.conftest import FIXTURES, REPO

GOLDEN = yaml.safe_load((FIXTURES / "golden.yaml").read_text(encoding="utf-8"))
NAME_RE = r"^(\d{4}|YYYY)-(\d{2}|MM)-(\d{2}|DD)_KKS(_[A-Za-z0-9-]+)*\.[a-z0-9]+$"


def test_split_boundaries_are_ordered():
    for case in GOLDEN["kirtan_splitter"]:
        assert 0 < case["kirtan_end"] <= case["class_start"], case["file"]


def test_trim_sections_are_ordered():
    for case in GOLDEN["class_trimmer"]:
        sections = case["sections"]
        assert all(s["start"] < s["end"] for s in sections)
        assert all(a["end"] <= b["start"] for a, b in zip(sections, sections[1:], strict=False))


def test_expected_names_follow_convention():
    import re

    names = [
        c[k]
        for group in ("kirtan_splitter", "class_discoverer")
        for c in GOLDEN[group]
        for k in ("kirtan_name", "class_name", "name")
        if c.get(k)
    ]
    assert names
    for name in names:
        assert re.match(NAME_RE, name), name
        assert 25 <= len(name) <= 128 or "YYYY" in name or "MM" in name, name


@pytest.mark.slow
def test_normalize_real_class_excerpt(settings, tmp_path):
    """Cut 5 minutes of the class from a real sample (stream copy), then normalize it."""
    case = GOLDEN["class_discoverer"][0]
    src = REPO / case["file"]
    if not src.exists():
        pytest.skip(f"{src} not available")
    start = case["class_start"]
    excerpt = settings.media_processed_files_path / "excerpt.mp3"
    stream_cut(Path(src), excerpt, [(start, start + 300)], dry_run=False)
    result = normalize_whole(
        excerpt, settings.media_processed_files_path / "norm.mp3", dry_run=False
    )
    assert result.qa_passed, result.to_dict()
    assert abs(result.after.integrated - TARGET_I) <= 1 and result.after.true_peak <= TARGET_TP
