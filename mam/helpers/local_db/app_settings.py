"""Settings changed in the media manager itself, stored in the local DB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mam.config import get_settings
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope

BASEROW_WRITES = "baserow_writes"


def get_setting(key: str, default: Any = None) -> Any:
    with session_scope() as s:
        row = s.get(m.AppSetting, key)
        return row.value.get("value", default) if row else default


def set_setting(key: str, value: Any) -> None:
    by = operator_name()
    with session_scope() as s:
        row = s.get(m.AppSetting, key)
        if row is None:
            row = m.AppSetting(key=key, value={})
            s.add(row)
        row.value = {"value": value}
        row.updated_by = by


def baserow_writes_enabled() -> bool:
    """The "Baserow writes" switch: off (rows are only logged) until switched on."""
    return bool(get_setting(BASEROW_WRITES, False))


def set_baserow_writes(enabled: bool) -> None:
    set_setting(BASEROW_WRITES, bool(enabled))


DEFAULT_STATUS = "default_status"


def default_status_setting() -> str:
    """The status filter the file list opens on; "__all" (All files) until chosen."""
    value = get_setting(DEFAULT_STATUS, "__all")
    return value if isinstance(value, str) and value else "__all"


def set_default_status(value: str) -> None:
    set_setting(DEFAULT_STATUS, value)


DEFAULT_SIZE = "default_size"


def default_size_setting() -> int:
    """How many files the file list's Show selector starts on; 50 until chosen."""
    value = get_setting(DEFAULT_SIZE, 50)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 50


def set_default_size(value: int) -> None:
    set_setting(DEFAULT_SIZE, int(value))


# ----------------------------------------------------------------------- Renamer options
RENAMER_MOVE = "renamer_move_files"
RENAMER_POSTFIX = "renamer_postfix"
POSTFIX_MAX_LEN = 30


def clean_postfix(value: str | None) -> str:
    """The postfix as it is added to a filename; raises ValueError if it cannot be used.

    Empty means no postfix. Only letters, digits, `_` and `-` are allowed, so a postfix can never
    add a dot (a second extension), a path separator or a character the filename rules forbid.
    """
    text = (value or "").strip()
    if len(text) > POSTFIX_MAX_LEN:
        raise ValueError(f"The postfix can be at most {POSTFIX_MAX_LEN} characters")
    if text and not all(c.isascii() and (c.isalnum() or c in "_-") for c in text):
        raise ValueError("The postfix can only have letters, digits, _ and -")
    return text


def renamer_move_files() -> bool:
    """ "Move files instead of copying": off until switched on."""
    return bool(get_setting(RENAMER_MOVE, False))


def renamer_postfix() -> str:
    """Postfix for the original that a copy leaves behind; empty (none) until set."""
    try:
        return clean_postfix(get_setting(RENAMER_POSTFIX, ""))
    except ValueError:
        return ""


def set_renamer_options(move_files: bool, postfix: str) -> None:
    postfix = clean_postfix(postfix)
    set_setting(RENAMER_MOVE, bool(move_files))
    set_setting(RENAMER_POSTFIX, postfix)


# ------------------------------------------------------------ Class discoverer options
TRANSCRIBE_SPEECH_ONLY = "transcribe_speech_only"


def transcribe_speech_only(default: bool = False) -> bool:
    """ "Only transcribe speech": the Settings choice, else the `.env` default."""
    value = get_setting(TRANSCRIBE_SPEECH_ONLY, None)
    return value if isinstance(value, bool) else default


def set_transcribe_speech_only(enabled: bool) -> None:
    set_setting(TRANSCRIBE_SPEECH_ONLY, bool(enabled))


# ------------------------------------------------------- options moved from .env
@dataclass(frozen=True)
class Option:
    """A non-secret option that was in `.env` and is now set on the Settings page.

    The value is stored in the local DB under `key` (the name of the Settings field); while
    nothing is stored, the `.env` value (or its default) is used."""

    key: str
    kind: str  # bool | int | float | text
    label: str
    group: str  # settings card: "Class discoverer", "Kirtan splitter", "Review", "General"
    tip: str
    low: float | None = None
    high: float | None = None
    restart: bool = False  # only read at start-up


OPTIONS: tuple[Option, ...] = (
    Option(
        "transcribe_glossary_fix",
        "bool",
        "Fix Sanskrit spellings",
        "Class discoverer",
        "Known Whisper misspellings (Harry Krishna) become the glossary's spelling.",
    ),
    Option(
        "transcribe_glossary_prompt",
        "bool",
        "Give the glossary to Whisper",
        "Class discoverer",
        "Whisper gets the glossary terms as its prompt. Can make it invent text.",
    ),
    Option(
        "transcribe_words",
        "bool",
        "Word timestamps",
        "Class discoverer",
        "Slower. Segment timestamps are enough for the tools.",
    ),
    Option(
        "transcribe_chunk_s",
        "int",
        "Chunk length (s)",
        "Class discoverer",
        "The recording is transcribed in pieces of about this length.",
        60,
        900,
    ),
    Option(
        "transcribe_chunk_timeout_s",
        "int",
        "Chunk timeout (s)",
        "Class discoverer",
        "A piece that takes longer is stopped and tried again.",
        60,
        1800,
    ),
    Option(
        "recorder_prefixes",
        "text",
        "Recorder file name prefixes",
        "Renamer",
        "Names such as ZOOM0004 or DSC_0012 that a recorder made are not descriptive. Prefixes "
        "separated by commas. R09-style names (R followed by two digits) always count.",
    ),
    Option(
        "recorder_albums",
        "text",
        "Recorder album tags to ignore",
        "Renamer",
        "An ID3 album that starts with one of these words (commas) is a recorder's name, not "
        "evidence.",
    ),
    Option(
        "other_speakers",
        "text",
        "Acronyms of other speakers",
        "Advanced",
        "A name with one of these (commas) is flagged, because WHO is always KKS.",
    ),
    Option(
        "noise_words",
        "text",
        "Words dropped from names",
        "Advanced",
        "Honorifics and format words (commas) that are left out of the WHAT.",
    ),
    Option(
        "min_year",
        "int",
        "Earliest year read as a date",
        "Advanced",
        "A year before this in a name is not taken as a date.",
        1900,
        2100,
    ),
    Option(
        "words_chapter",
        "text",
        "Other words for \u201cchapter\u201d",
        "Advanced",
        "Words (commas) that count like chapter when a verse is announced, e.g. hoofdstuk, "
        "capitulo. Numbers must be spoken as digits or English words.",
    ),
    Option(
        "words_verse",
        "text",
        "Other words for \u201ctext\u201d (verse)",
        "Advanced",
        "Words (commas) that count like text or verse, e.g. vers, versiculo.",
    ),
    Option(
        "transcribe_languages",
        "text",
        "Languages Whisper may choose",
        "Class discoverer",
        "Two-letter codes separated by commas (en,nl,de,...).",
    ),
    Option(
        "vedabase_delay_s",
        "float",
        "Seconds between vedabase.io requests",
        "Class discoverer",
        "At least 10: the crawl delay in vedabase.io's robots.txt.",
        10,
        120,
    ),
    Option(
        "analysis_early_stop",
        "bool",
        "Stop listening once the class is found",
        "Kirtan splitter",
        "The cut cannot change after that. Faster.",
        None,
        None,
    ),
    Option(
        "analysis_in_processes",
        "bool",
        "Analyse in separate processes",
        "Kirtan splitter",
        "Several files at once. Off: in the worker threads.",
    ),
    Option(
        "spot_check_rate",
        "float",
        "Random check rate",
        "Review",
        "Share of sure decisions that are shown for review anyway (0 = off, 0.05 = 1 in 20).",
        0,
        1,
    ),
    Option("operator_name", "text", "Operator name", "General", "Recorded on approvals and saves."),
    Option(
        "workers_ml",
        "int",
        "Transcription workers",
        "General",
        "Applies after restarting mam serve.",
        1,
        4,
        True,
    ),
    Option(
        "workers_cpu",
        "int",
        "CPU workers (ffmpeg, splitter)",
        "General",
        "Applies after restarting mam serve.",
        1,
        32,
        True,
    ),
    Option(
        "workers_io",
        "int",
        "Copy and Baserow workers",
        "General",
        "Applies after restarting mam serve.",
        1,
        16,
        True,
    ),
)
OPTION_BY_KEY = {o.key: o for o in OPTIONS}


def parse_option(option: Option, raw: Any) -> Any:
    """Validate a value from the Settings page; raises ValueError with a plain message."""
    if option.kind == "bool":
        return raw in (True, "on", "true", "1")
    text = str(raw if raw is not None else "").strip()
    if option.kind == "text":
        if not text and option.key not in ("words_chapter", "words_verse"):
            raise ValueError("This cannot be empty")
        if option.key == "transcribe_languages":
            codes = [c.strip().lower() for c in text.split(",") if c.strip()]
            if not codes or any(not (len(c) == 2 and c.isalpha()) for c in codes):
                raise ValueError("Use two-letter language codes separated by commas")
            return ",".join(codes)
        if option.key in ("recorder_prefixes", "recorder_albums", "other_speakers", "noise_words"):
            words = [w.strip() for w in text.split(",") if w.strip()]
            if not words or any(not re.fullmatch(r"[A-Za-z0-9_-]+", w) for w in words):
                raise ValueError("Use words (letters, digits, - and _) separated by commas")
            return ",".join(words)
        return text
    try:
        value = int(text) if option.kind == "int" else float(text.replace(",", "."))
    except ValueError:
        raise ValueError("Enter a number") from None
    if option.low is not None and value < option.low:
        raise ValueError(f"At least {option.low:g}")
    if option.high is not None and value > option.high:
        raise ValueError(f"At most {option.high:g}")
    return value


def option_value(option: Option, settings: Any = None) -> Any:
    """The stored value, else the `.env` value."""
    stored = get_setting(option.key, None)
    if stored is not None:
        return stored
    return getattr(settings or get_settings(), option.key)


def set_option(key: str, raw: Any) -> Any:
    option = OPTION_BY_KEY[key]
    value = parse_option(option, raw)
    set_setting(key, value)
    return value


def tuned(settings: Any = None) -> Any:
    """`settings` with the options stored on the Settings page applied (a copy)."""
    base = settings or get_settings()
    update = {}
    for option in OPTIONS:
        stored = get_setting(option.key, None)
        if stored is not None:
            update[option.key] = stored
    return base.model_copy(update=update) if update else base


def operator_name() -> str:
    stored = get_setting("operator_name", None)
    return str(stored) if stored else get_settings().operator_name


def option_groups() -> dict[str, list[Option]]:
    groups: dict[str, list[Option]] = {}
    for option in OPTIONS:
        groups.setdefault(option.group, []).append(option)
    return groups
