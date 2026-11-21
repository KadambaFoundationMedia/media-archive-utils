from __future__ import annotations

import httpx
import pytest

from mam.tools.class_discoverer.vedabase import (
    VedabaseClient,
    VedabaseError,
    VerseNotFound,
    VerseRef,
    parse_verse_html,
    parse_what,
)
from tests.conftest import FIXTURES

VB = FIXTURES / "vedabase"


def html(name: str) -> str:
    return (VB / f"{name}.html").read_text(encoding="utf-8")


def test_parse_single_verses():
    assert parse_what("BG-2-51") == [VerseRef("bg", None, (2, 51))]
    assert parse_what("SB-9-20-20-Dont-waste-time") == [VerseRef("sb", None, (9, 20, 20))]
    assert parse_what("CC-Madhya-20-154") == [VerseRef("cc", "madhya", (20, 154))]


def test_parse_ranges_expand_per_verse():
    refs = parse_what("SB-9-20-20-22")
    assert [r.numbers for r in refs] == [(9, 20, 20), (9, 20, 21), (9, 20, 22)]
    assert [r.numbers for r in parse_what("BG-2-51-53")][-1] == (2, 53)
    assert len(parse_what("CC-Madhya-20-154-156")) == 3


@pytest.mark.parametrize("what", ["Kirtan", "SB-9-20", "SB-9-20-27-20", "CC-20-154", "", "SB"])
def test_parse_what_rejects_invalid(what):
    assert parse_what(what) == []


def test_ref_url_and_tag():
    assert parse_what("CC-Madhya-20-154")[0].url == (
        "https://vedabase.io/en/library/cc/madhya/20/154/"
    )
    assert parse_what("CC-Madhya-20-154")[0].tag == "Madhya 20.154"
    assert parse_what("SB-9-20-20")[0].tag == "9.20.20"


@pytest.mark.parametrize(
    ("name", "heading", "first_word", "translation_start"),
    [
        ("bg-2-51", "Bg. 2.51", "karma-jaṁ", "By thus engaging"),
        ("sb-9-20-20", "ŚB 9.20.20", "yadā", "When the King refused"),
        ("sb-1-19-31", "ŚB 1.19.31", None, None),
        ("sb-3-6-6", "ŚB 3.6.6", None, None),
        ("cc-madhya-20-154", "CC Madhya 20.154", "īśvaraḥ", None),
    ],
)
def test_parse_fixture_pages(name, heading, first_word, translation_start):
    v = parse_verse_html(html(name), name)
    assert v.heading == heading
    assert v.verse_text and v.synonyms and v.translation
    assert "Verse text" not in v.verse_text and "Synonyms" not in v.synonyms
    assert not v.translation.startswith("Translation")
    if first_word:
        assert v.verse_text.startswith(first_word)
    if translation_start:
        assert v.translation.startswith(translation_start)


def test_parse_keeps_verse_lines_and_original_script():
    v = parse_verse_html(html("bg-2-51"))
    assert len(v.verse_text.splitlines()) == 4
    assert "कर्मजं" in v.original
    cc = parse_verse_html(html("cc-madhya-20-154"))
    assert "ঈশ্বরঃ" in cc.original  # CC pages use Bengali
    assert "Brahma-saṁhitā" in cc.purport


def test_parse_unexpected_page_raises():
    with pytest.raises(VedabaseError):
        parse_verse_html("<html><h1>Oops</h1></html>", "x")


def _client(handler, delay=0.0):
    return VedabaseClient(
        delay_s=delay, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_client_fetches_once_then_uses_cache():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, text=html("bg-2-51"))

    c = _client(handler)
    ref = parse_what("BG-2-51")[0]
    assert c.cached(ref) is None
    first = c.get(ref)
    second = c.get(ref)
    assert first == second and len(calls) == 1
    assert calls[0] == "https://vedabase.io/en/library/bg/2/51/"


def test_client_404_and_errors():
    c = _client(lambda r: httpx.Response(404))
    with pytest.raises(VerseNotFound):
        c.get(parse_what("SB-9-20-999")[0])
    c = _client(lambda r: httpx.Response(503))
    with pytest.raises(VedabaseError):
        c.get(parse_what("SB-9-20-1")[0])


def test_client_waits_between_requests(monkeypatch):
    slept = []
    monkeypatch.setattr("mam.tools.class_discoverer.vedabase.time.sleep", slept.append)
    c = _client(lambda r: httpx.Response(200, text=html("bg-2-51")), delay=10)
    c.get(parse_what("BG-2-51")[0])
    c.get(parse_what("BG-2-52")[0])
    assert len(slept) == 1 and 9 < slept[0] <= 10
