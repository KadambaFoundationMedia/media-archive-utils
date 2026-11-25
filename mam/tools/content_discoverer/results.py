"""The themes and YouTube description kept with a file: shown and edited in the file page's
form. The record's `meta["content_discoverer"]` holds the run's values, and under `approved`
what a person approved or saved."""

from __future__ import annotations

from typing import Any

from mam.helpers.local_db import models as m


def stored_results(record: m.MediaFile) -> dict[str, Any] | None:
    """The approved values, else what the run wrote; None for a file without results."""
    cd = (record.meta or {}).get("content_discoverer")
    if not cd:
        return None
    shown = {k: cd.get(k) for k in ("topics", "title", "description", "footer")}
    approved = cd.get("approved") or {}
    shown.update({k: v for k, v in approved.items() if v is not None})
    shown.setdefault("not_replaced", [])
    return shown


def parse_themes(text: str) -> list[str]:
    """A person's themes, taken as typed: separated by commas."""
    return [t.strip() for t in str(text or "").split(",") if t.strip()]


def clean_description(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").strip()  # a browser form sends CRLF
