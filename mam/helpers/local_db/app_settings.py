"""Settings changed in the media manager itself, stored in the local DB."""

from __future__ import annotations

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
    with session_scope() as s:
        row = s.get(m.AppSetting, key)
        if row is None:
            row = m.AppSetting(key=key, value={})
            s.add(row)
        row.value = {"value": value}
        row.updated_by = get_settings().operator_name


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
