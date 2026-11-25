"""Write the ID3 tags of a processed copy (docs/references/idv3-tags.md, SPEC.md section 4).

The nine fields are always set from the Renamer's data; a field with no value is removed, so a
recorder's old tag never survives. Other tags (cover art, lyrics) are left alone.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from mam.errors import MamError
from mam.helpers.file_organizer.organizer import assert_writable
from mam.tools.renamer.countries import country_name
from mam.tools.renamer.parse import find_verse

log = logging.getLogger(__name__)

PERSON = "Kadamba Kanana Swami"
PUBLISHER = "Kadamba Foundation"  # Publisher and Copyright (decided 2026-11-25)
FORMATS = {"mp3", "m4a", "wma"}


def _spaces(text: str | None) -> str:
    return (text or "").replace("-", " ").replace("_", " ").strip()


def _date_parts(when: str | None) -> tuple[str | None, str]:
    """The year (None when unknown) and the known part of the date ('2015-07' for 2015-07-DD)."""
    known = [p for p in (when or "").split("-") if p.isdigit()]
    year = known[0] if known and len(known[0]) == 4 else None
    return year, "-".join(known) if year else ""


# ID3 wants ISO 639-2 codes (three lowercase letters); Whisper's codes have two.
ISO_639_2 = {
    "en": "eng", "nl": "dut", "de": "ger", "es": "spa", "hi": "hin", "fr": "fre", "cs": "cze",
    "sk": "slo", "ru": "rus", "pt": "por", "it": "ita", "pl": "pol", "hr": "hrv", "sl": "slv",
    "sr": "srp", "hu": "hun", "lt": "lit", "lv": "lav", "et": "est", "fi": "fin", "sv": "swe",
    "no": "nor", "da": "dan", "bn": "ben", "ta": "tam", "uk": "ukr", "ro": "rum", "bg": "bul",
    "tr": "tur", "zh": "chi", "sa": "san",
}  # fmt: skip


def language_codes(names: list[str] | None, ref: Any = None) -> str:
    """ISO 639-2 codes (`eng`) of the language names, comma separated. The language name is
    found through the `whisper_code` column of `language_terms`, else the built-in list; a
    language with no ISO 639-2 code here is left out."""
    from mam.tools.class_discoverer.tool import FALLBACK_LANGUAGE_NAMES

    table = dict(FALLBACK_LANGUAGE_NAMES)
    table.update(getattr(ref, "aliases", {}).get("whisper_codes") or {})
    by_name = {name.lower(): code for code, name in table.items()}
    codes = [
        ISO_639_2[by_name[n.lower()]]
        for n in names or []
        if n and by_name.get(n.lower()) in ISO_639_2
    ]
    return ",".join(dict.fromkeys(codes))


def build_tags(
    data: dict[str, Any], genre: str | None, country: str | None, languages: str | None = None
) -> dict[str, str]:
    """The tag values for a Renamer proposal. Fields with no value are left out."""
    what = data.get("what") or ""
    year, date_text = _date_parts(data.get("when"))
    category = data.get("category") or None
    verse = find_verse(what) if what else None
    if verse and not verse.numbers:
        verse = None

    # Title: the verse as in titles (SB 9.20.20-27) and the short title; else the WHAT as words.
    if verse:
        rest = _spaces(what[verse.end :].strip(" -_."))
        title = f"{verse.readable} - {rest}" if rest else verse.readable
    else:
        title = _spaces(what)

    # Album: the book's own grouping for a verse, else the category and year.
    album = None
    if verse and verse.book == "SB":
        album = f"SB Canto {verse.numbers[0]}"
    elif verse and verse.book == "BG":
        album = f"BG chapter {verse.numbers[0]}"
    elif verse and verse.book == "CC" and verse.lila:
        album = f"CC {verse.lila}-lila"
    elif category:
        album = f"{category} - {year}" if year else category

    place = _spaces(data.get("place"))
    comment = ", ".join(p for p in (date_text, place, _spaces(country)) if p)
    tags = {
        "title": title,
        "artist": PERSON,
        "album": album or "",
        "genre": genre or category or "",
        "album_artist": PERSON,
        "composer": PERSON,
        "year": year or "",
        "comment": comment,
        "publisher": PUBLISHER,
        "copyright": f"{year or date.today().year} {PUBLISHER}",  # ID3: must begin with a year
        "language": languages or "",
    }
    return {k: v for k, v in tags.items() if v}


def country_for(data: dict[str, Any], country_options: dict[str, str]) -> str | None:
    cc = (data.get("cc") or "").lower()
    if not cc:
        return None
    return country_options.get(cc) or country_name(cc)


def write_tags(path: Path, tags: dict[str, str]) -> None:
    """Write `tags` to the file; fields missing from `tags` are removed."""
    ext = path.suffix.lower().lstrip(".")
    if ext not in FORMATS:
        raise MamError(f"Tags are not written to .{ext} files")
    assert_writable(path)
    if ext == "mp3":
        _write_mp3(path, tags)
    elif ext == "m4a":
        _write_m4a(path, tags)
    else:
        _write_wma(path, tags)


def _write_mp3(path: Path, tags: dict[str, str]) -> None:
    from mutagen import id3
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        audio = ID3(path)
    except ID3NoHeaderError:
        audio = ID3()
    frames = {
        "title": ("TIT2", id3.TIT2),
        "artist": ("TPE1", id3.TPE1),
        "album": ("TALB", id3.TALB),
        "genre": ("TCON", id3.TCON),
        "album_artist": ("TPE2", id3.TPE2),
        "composer": ("TCOM", id3.TCOM),
        "year": ("TDRC", id3.TDRC),
        "publisher": ("TPUB", id3.TPUB),
        "copyright": ("TCOP", id3.TCOP),
    }
    for key, (name, cls) in frames.items():
        audio.delall(name)
        if key in tags:
            audio.add(cls(encoding=3, text=[tags[key]]))
    audio.delall("TLAN")
    if "language" in tags:
        audio.add(id3.TLAN(encoding=3, text=tags["language"].split(",")))
    audio.delall("COMM")
    if "comment" in tags:
        audio.add(id3.COMM(encoding=3, lang="eng", desc="", text=[tags["comment"]]))
    audio.delall("TRCK")  # Track stays empty
    audio.save(path, v2_version=4)


def _write_m4a(path: Path, tags: dict[str, str]) -> None:
    from mutagen.mp4 import MP4, MP4FreeForm

    audio = MP4(path)
    keys = {
        "title": "\xa9nam",
        "artist": "\xa9ART",
        "album": "\xa9alb",
        "genre": "\xa9gen",
        "album_artist": "aART",
        "composer": "\xa9wrt",
        "year": "\xa9day",
        "comment": "\xa9cmt",
        "copyright": "cprt",
        "publisher": "----:com.apple.iTunes:PUBLISHER",
        "language": "----:com.apple.iTunes:LANGUAGE",
    }
    if audio.tags is None:
        audio.add_tags()
    for key, atom in keys.items():
        if key not in tags:
            audio.tags.pop(atom, None)
        elif atom.startswith("----"):  # free-form atoms hold bytes
            audio.tags[atom] = [MP4FreeForm(tags[key].encode("utf-8"))]
        else:
            audio.tags[atom] = [tags[key]]
    audio.tags.pop("trkn", None)
    audio.save()


def _write_wma(path: Path, tags: dict[str, str]) -> None:
    from mutagen.asf import ASF

    audio = ASF(path)
    keys = {
        "title": "Title",
        "artist": "Author",
        "album": "WM/AlbumTitle",
        "genre": "WM/Genre",
        "album_artist": "WM/AlbumArtist",
        "composer": "WM/Composer",
        "year": "WM/Year",
        "comment": "Description",
        "publisher": "WM/Publisher",
        "copyright": "Copyright",
        "language": "WM/Language",
    }
    for key, atom in keys.items():
        if key in tags:
            audio.tags[atom] = [tags[key]]
        else:
            audio.tags.pop(atom, None)
    for atom in ("WM/TrackNumber", "WM/Track"):
        audio.tags.pop(atom, None)
    audio.save()


def apply_tags(
    path: Path,
    data: dict[str, Any],
    ref: Any,
    *,
    dry_run: bool,
    media_file_id: int | None,
    tool: str,
) -> dict[str, Any]:
    """Build and write the tags of the processed copy at `path`.

    Never raises: a file that cannot be tagged is a warning, the rename stays done.
    Returns {"tags": {...}, "written": bool, "warning": str | None}.
    """
    from mam.helpers.local_db.db import session_scope
    from mam.helpers.local_db.repo import record_event

    ext = path.suffix.lower().lstrip(".")
    if ext not in FORMATS:
        return {"tags": {}, "written": False, "warning": None, "skipped": f"no tags for .{ext}"}
    cat = ref.matcher.by_name(data.get("category") or "") if data.get("category") else None
    tags = build_tags(
        data,
        getattr(cat, "genre", None),
        country_for(data, ref.country_options),
        language_codes(data.get("languages"), ref),
    )
    result: dict[str, Any] = {"tags": tags, "written": False, "warning": None}
    if dry_run:
        return result
    try:
        write_tags(path, tags)
        result["written"] = True
    except Exception as exc:  # noqa: BLE001 - a tagging problem must not undo the rename
        log.warning("Could not write tags to %s: %s", path, exc)
        result["warning"] = f"Tags could not be written: {exc}"
    with session_scope() as s:
        record_event(
            s,
            action="write_tags",
            tool=tool,
            dry_run=False,
            media_file_id=media_file_id,
            dst=path,
            detail={"tags": tags, "written": result["written"], "error": result["warning"]},
        )
    return result
