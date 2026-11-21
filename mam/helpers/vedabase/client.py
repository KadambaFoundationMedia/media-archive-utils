"""Fetch verse pages from vedabase.io: cached for good, polite (robots.txt crawl delay 10 s)."""

from __future__ import annotations

import logging
import threading
import time

import httpx
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import VedabaseError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope
from mam.helpers.vedabase.parse import Verse, parse_verse_page
from mam.helpers.vedabase.refs import VerseRef

log = logging.getLogger(__name__)
USER_AGENT = "media-archive-utils/1.0 (Kadamba Foundation archive tools; local use)"

_lock = threading.Lock()  # one request at a time, and at least `delay` seconds apart
_last_request = 0.0


class VedabaseClient:
    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._http = http or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def url(self, ref: VerseRef) -> str:
        return self.settings.vedabase_url.rstrip("/") + ref.path

    def cached(self, ref: VerseRef, session: Session | None = None) -> tuple[bool, Verse | None]:
        """(known, verse): known is False when the page was never fetched."""
        url = self.url(ref)
        if session is None:
            with session_scope() as own:
                return self.cached(ref, own)
        row = session.get(m.VedabaseCache, url)
        if row is None:
            return False, None
        return True, Verse.from_dict(row.data) if row.status == 200 and row.data else None

    def fetch(self, ref: VerseRef) -> Verse | None:
        """The verse page, from the cache or from vedabase.io (None when the verse does not
        exist). Network use is serialized and spaced by `vedabase_delay_s`."""
        known, verse = self.cached(ref)
        if known:
            return verse
        url = self.url(ref)
        global _last_request
        with _lock:
            known, verse = self.cached(ref)  # another thread may have fetched it meanwhile
            if known:
                return verse
            wait = _last_request + self.settings.vedabase_delay_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._http.get(url, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                raise VedabaseError(f"vedabase.io could not be reached: {exc}") from exc
            finally:
                _last_request = time.monotonic()
        if response.status_code == 404:
            self._store(url, 404, None)
            return None
        if response.status_code != 200:
            raise VedabaseError(f"vedabase.io answered {response.status_code} for {url}")
        verse = parse_verse_page(response.text)
        if verse is None:
            raise VedabaseError(f"No verse found on {url}: the page layout may have changed")
        self._store(url, 200, verse)
        return verse

    @staticmethod
    def _store(url: str, status: int, verse: Verse | None) -> None:
        with session_scope() as session:
            row = session.get(m.VedabaseCache, url)
            data = verse.to_dict() if verse else None
            if row is None:
                session.add(m.VedabaseCache(url=url, status=status, data=data))
            else:
                row.status, row.data = status, data
