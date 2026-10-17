"""Overwrite policy for updating existing media rows (SPEC.md section 6).

- Only empty fields are filled; values a person entered are never overwritten.
- `notes` gets a dated entry appended.
- Multiple-select fields (e.g. `language`) are merged as a union.
Conflicts (a different non-empty value) are returned so they can be proposed on the review page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

APPEND_FIELDS = {"notes", "alternative_links"}


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value is False


def _plain(value: Any) -> Any:
    """Reduce Baserow select objects ({"id", "value", "color"}) to their value."""
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


@dataclass
class MergeResult:
    payload: dict[str, Any] = field(default_factory=dict)
    conflicts: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # field -> (current, new)


def merge_update(
    current: dict[str, Any],
    proposed: dict[str, Any],
    *,
    multi_select_fields: set[str],
    tool: str,
    today: date | None = None,
) -> MergeResult:
    result = MergeResult()
    stamp = (today or date.today()).isoformat()
    for name, new in proposed.items():
        if is_empty(new):
            continue
        old = _plain(current.get(name))
        if name in APPEND_FIELDS:
            entry = f"[{stamp} {tool}] {new}"
            if old and str(new) in str(old):
                continue
            result.payload[name] = f"{old}\n{entry}" if old else entry
        elif name in multi_select_fields:
            old_list = list(old or [])
            merged = old_list + [v for v in new if v not in old_list]
            if merged != old_list:
                result.payload[name] = merged
        elif is_empty(old):
            result.payload[name] = new
        elif str(old) != str(new):
            result.conflicts[name] = (old, new)
    return result
