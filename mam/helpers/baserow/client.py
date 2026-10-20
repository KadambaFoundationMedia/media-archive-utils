"""Baserow REST client with retries, schema validation, dry-run and a write log for undo."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from mam.config import Settings, get_settings
from mam.errors import BaserowError
from mam.helpers.baserow.merge import MergeResult, merge_update
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope

log = logging.getLogger(__name__)

READ_ONLY_TYPES = {
    "created_on",
    "last_modified",
    "last_modified_by",
    "created_by",
    "formula",
    "lookup",
    "rollup",
    "count",
    "autonumber",
    "uuid",
}
SELECT_TYPES = {"single_select", "multiple_select"}
RETRY_STATUS = {429, 500, 502, 503, 504}


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUS
    return False


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    options: dict[str, int]  # select option value -> id

    @property
    def writable(self) -> bool:
        return self.type not in READ_ONLY_TYPES


@dataclass
class WriteResult:
    log_id: int
    dry_run: bool
    row_id: int | None
    payload: dict[str, Any]
    row: dict[str, Any] | None = None
    conflicts: dict[str, tuple[Any, Any]] | None = None


class UnknownOption(BaserowError):
    """A select value is not an existing option (e.g. a new place or country)."""

    def __init__(self, field_name: str, value: str) -> None:
        super().__init__(f"'{value}' is not an option of {field_name}")
        self.field_name = field_name
        self.value = value


class BaserowClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        apply: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.baserow_api_token:
            raise BaserowError("BASEROW_API_TOKEN is not set in .env")
        # Writes are only sent when the "Baserow writes" switch is on (SPEC.md section 6).
        if apply is None:
            from mam.helpers.local_db.app_settings import baserow_writes_enabled

            apply = baserow_writes_enabled()
        self.apply = apply
        self._http = httpx.Client(
            base_url=self.settings.baserow_api_url.rstrip("/"),
            headers={"Authorization": f"Token {self.settings.baserow_api_token}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )
        self._fields: dict[int, dict[str, Field]] = {}

    # ---------------------------------------------------------------- tables
    @property
    def media_table(self) -> int:
        return self._table_id(self.settings.baserow_media_table_id, "BASEROW_MEDIA_TABLE_ID")

    @property
    def travel_table(self) -> int:
        return self._table_id(
            self.settings.baserow_travel_schedule_table_id, "BASEROW_TRAVEL_SCHEDULE_TABLE_ID"
        )

    @property
    def category_table(self) -> int:
        return self._table_id(
            self.settings.baserow_category_terms_table_id, "BASEROW_CATEGORY_TERMS_TABLE_ID"
        )

    @property
    def place_country_table(self) -> int:
        return self._table_id(
            self.settings.baserow_place_country_table_id, "BASEROW_PLACE_COUNTRY_TABLE_ID"
        )

    @staticmethod
    def _table_id(value: int | None, name: str) -> int:
        if value is None:
            raise BaserowError(f"{name} is not set in .env")
        return value

    # ------------------------------------------------------------------ http
    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._http.request(method, url, **kwargs)
        if response.status_code >= 400 and response.status_code not in RETRY_STATUS:
            raise BaserowError(
                f"Baserow {method} {url} failed: {response.status_code} {response.text}"
            )
        response.raise_for_status()
        return response.json() if response.content else None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BaserowClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- schema
    def fields(self, table_id: int, refresh: bool = False) -> dict[str, Field]:
        """Field schema, refreshed once per client (the media schema changes often)."""
        if refresh or table_id not in self._fields:
            data = self._request("GET", f"/api/database/fields/table/{table_id}/")
            self._fields[table_id] = {
                f["name"]: Field(
                    name=f["name"],
                    type=f["type"],
                    options={o["value"]: o["id"] for o in f.get("select_options") or []},
                )
                for f in data
            }
        return self._fields[table_id]

    def to_payload(self, table_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """Validate column names and select values, and convert select values to option ids."""
        fields = self.fields(table_id)
        payload: dict[str, Any] = {}
        for name, value in values.items():
            field = fields.get(name)
            if field is None:
                raise BaserowError(f"Unknown column '{name}' in table {table_id}")
            if not field.writable:
                raise BaserowError(f"Column '{name}' is read-only ({field.type})")
            if field.type in SELECT_TYPES and value is not None:
                items = value if isinstance(value, list) else [value]
                ids = []
                for item in items:
                    if isinstance(item, int):
                        ids.append(item)
                    elif item in field.options:
                        ids.append(field.options[item])
                    else:
                        raise UnknownOption(name, item)
                ids = list(dict.fromkeys(ids))  # never send an option twice
                payload[name] = ids if field.type == "multiple_select" else ids[0]
            else:
                payload[name] = value
        return payload

    # ----------------------------------------------------------------- reads
    def get_row(self, table_id: int, row_id: int) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/database/rows/table/{table_id}/{row_id}/",
            params={"user_field_names": "true"},
        )

    def iter_rows(
        self,
        table_id: int,
        *,
        search: str | None = None,
        filters: dict[str, Any] | None = None,
        page_size: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """Iterate rows. `filters` maps 'field__type' (e.g. 'date__date_equal') to a value."""
        params: dict[str, Any] = {"user_field_names": "true", "size": page_size}
        if search:
            params["search"] = search
        for key, value in (filters or {}).items():
            name, _, kind = key.partition("__")
            params[f"filter__{name}__{kind or 'equal'}"] = value
        page = 1
        while True:
            data = self._request(
                "GET", f"/api/database/rows/table/{table_id}/", params={**params, "page": page}
            )
            yield from data["results"]
            if not data.get("next"):
                return
            page += 1

    def find_media(
        self,
        *,
        date: str | None = None,
        category: str | None = None,
        place: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        """Candidate media rows matching any given date/category/place/title."""
        filters: dict[str, Any] = {}
        if date:
            filters["date__date_equal"] = date
        if category:
            filters["category__single_select_equal"] = self.fields(self.media_table)[
                "category"
            ].options.get(category, category)
        if place:
            filters["place_location__single_select_equal"] = self.fields(self.media_table)[
                "place_location"
            ].options.get(place, place)
        return list(self.iter_rows(self.media_table, search=title, filters=filters))

    def travel_schedule(self) -> list[dict[str, Any]]:
        return list(self.iter_rows(self.travel_table))

    def category_terms(self) -> list[dict[str, Any]]:
        return list(self.iter_rows(self.category_table))

    def place_country(self) -> list[dict[str, Any]]:
        """The place -> country table (one row per media place_location option)."""
        return list(self.iter_rows(self.place_country_table))

    # ---------------------------------------------------------------- writes
    def _log(self, **kwargs: Any) -> int:
        with session_scope() as session:
            entry = m.BaserowWriteLog(**kwargs)
            session.add(entry)
            session.flush()
            return entry.id

    def create_row(
        self,
        table_id: int,
        values: dict[str, Any],
        *,
        tool: str,
        media_file_id: int | None = None,
        dry_run: bool | None = None,
    ) -> WriteResult:
        dry = (not self.apply) if dry_run is None else dry_run
        payload = self.to_payload(table_id, values)
        row = None
        if not dry:
            row = self._request(
                "POST",
                f"/api/database/rows/table/{table_id}/",
                params={"user_field_names": "true"},
                json=payload,
            )
        log_id = self._log(
            media_file_id=media_file_id,
            table_id=table_id,
            row_id=row["id"] if row else None,
            action="create",
            before=None,
            after=row,
            payload=values,
            dry_run=dry,
            tool=tool,
        )
        log.info(
            "Baserow create%s in table %s",
            " (dry-run)" if dry else "",
            table_id,
            extra={"data": values},
        )
        return WriteResult(log_id, dry, row["id"] if row else None, values, row)

    def update_row(
        self,
        table_id: int,
        row_id: int,
        values: dict[str, Any],
        *,
        tool: str,
        media_file_id: int | None = None,
        dry_run: bool | None = None,
        merge: bool = True,
        overwrite: set[str] | frozenset = frozenset(),
    ) -> WriteResult:
        """Update a row. With `merge` (default) the fill-empty/append/union policy applies;
        fields in `overwrite` replace their old value."""
        dry = (not self.apply) if dry_run is None else dry_run
        before = self.get_row(table_id, row_id)
        conflicts = None
        if merge:
            multi = {f.name for f in self.fields(table_id).values() if f.type == "multiple_select"}
            merged: MergeResult = merge_update(
                before, values, multi_select_fields=multi, tool=tool, overwrite=overwrite
            )
            values, conflicts = merged.payload, merged.conflicts
        if not values:
            return WriteResult(0, dry, row_id, {}, before, conflicts)
        payload = self.to_payload(table_id, values)
        after = None
        if not dry:
            after = self._request(
                "PATCH",
                f"/api/database/rows/table/{table_id}/{row_id}/",
                params={"user_field_names": "true"},
                json=payload,
            )
        log_id = self._log(
            media_file_id=media_file_id,
            table_id=table_id,
            row_id=row_id,
            action="update",
            before={k: before.get(k) for k in values},
            after=after,
            payload=values,
            dry_run=dry,
            tool=tool,
        )
        log.info(
            "Baserow update%s row %s", " (dry-run)" if dry else "", row_id, extra={"data": values}
        )
        return WriteResult(log_id, dry, row_id, values, after, conflicts)

    def delete_row(
        self, table_id: int, row_id: int, *, tool: str, dry_run: bool | None = None
    ) -> WriteResult:
        dry = (not self.apply) if dry_run is None else dry_run
        before = self.get_row(table_id, row_id)
        if not dry:
            self._request("DELETE", f"/api/database/rows/table/{table_id}/{row_id}/")
        log_id = self._log(
            table_id=table_id,
            row_id=row_id,
            action="delete",
            before=before,
            after=None,
            payload=None,
            dry_run=dry,
            tool=tool,
        )
        return WriteResult(log_id, dry, row_id, {}, None)

    def undo(self, log_id: int, *, dry_run: bool | None = None) -> WriteResult:
        """Revert a logged write: delete a created row, or restore the previous field values."""
        with session_scope() as session:
            entry = session.get(m.BaserowWriteLog, log_id)
            if entry is None:
                raise BaserowError(f"No Baserow write with id {log_id}")
            if entry.dry_run or entry.undone:
                raise BaserowError(f"Write {log_id} was a dry run or is already undone")
            action, table_id, row_id, before = (
                entry.action,
                entry.table_id,
                entry.row_id,
                entry.before or {},
            )
        if action == "create":
            result = self.delete_row(table_id, row_id, tool="undo", dry_run=dry_run)
        elif action == "update":
            fields = self.fields(table_id)
            restore = {}
            for name, value in before.items():
                if fields[name].type in SELECT_TYPES:
                    value = (
                        [v["id"] for v in value]
                        if isinstance(value, list)
                        else (value["id"] if value else None)
                    )
                restore[name] = value
            result = self.update_row(
                table_id, row_id, restore, tool="undo", dry_run=dry_run, merge=False
            )
        else:
            raise BaserowError("Deleted rows cannot be restored automatically; see the write log")
        if not result.dry_run:
            with session_scope() as session:
                session.get(m.BaserowWriteLog, log_id).undone = True
        return result
