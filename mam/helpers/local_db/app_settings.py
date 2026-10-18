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
