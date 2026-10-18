"""Reference data for the Renamer: places, countries, travel schedule and the media index.

Loaded from Baserow once and cached in memory (and on disk, so the Renamer still works when
Baserow is unreachable). Learned alias rules from the local DB are merged in.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from mam.config import get_settings
from mam.helpers.file_organizer import CategoryMatcher
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import session_scope
from mam.tools.renamer.countries import cc_for_name
from mam.tools.renamer.dates import fold

log = logging.getLogger(__name__)

ALIASES_FILE = Path(__file__).with_name("aliases.yaml")
CACHE_TTL_S = 600


def norm_key(text: str) -> str:
    return re.sub(r"[\s_\-.,()]+", " ", fold(text)).strip()


def display_place(name: str) -> str:
    """Place as written in a filename: 'ISKCON-Ljubljana' -> 'Ljubljana'."""
    name = re.sub(r"^ISKCON-", "", name, flags=re.I)
    parts = [p for p in re.split(r"[\s_\-]+", name) if p]
    return "-".join(p if any(c.isupper() for c in p[1:]) else p[:1].upper() + p[1:] for p in parts)


@dataclass
class Place:
    name: str  # Baserow place_location value, or the raw name when unknown
    country: str | None  # Baserow country option, e.g. 'Norway'
    cc: str | None
    known: bool = True

    @property
    def display(self) -> str:
        return display_place(self.name)


@dataclass(frozen=True)
class Stay:
    start: date
    end: date
    place: str
    country: str | None
    cc: str | None

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass
class MediaRow:
    id: int
    date: str | None
    category: str | None
    place: str | None
    country: str | None
    tag: str | None
    title: str | None


@dataclass
class Reference:
    matcher: CategoryMatcher
    places: dict[str, Place]  # norm key -> place (names, short names and aliases)
    stays: list[Stay]
    media_by_date: dict[str, list[MediaRow]]
    place_options: set[str]
    country_options: dict[str, str]  # cc -> Baserow country option
    category_options: set[str]
    language_options: set[str]
    aliases: dict[str, Any]
    max_place_words: int = 3
    loaded_at: float = field(default_factory=time.time)

    def find_place(self, text: str) -> Place | None:
        return self.places.get(norm_key(text))

    def stays_on(self, d: date) -> list[Stay]:
        return [s for s in self.stays if s.covers(d)]

    def category_hint(self, text: str) -> str | None:
        return self.aliases["categories"].get(norm_key(text))

    def language(self, token: str) -> str | None:
        return self.aliases["languages"].get(norm_key(token))

    def country_option(self, cc: str | None) -> str | None:
        return self.country_options.get(cc) if cc else None


_lock = threading.Lock()
_cached: Reference | None = None


def _cache_file() -> Path:
    return get_settings().local_db_path.parent / "cache" / "baserow_reference.json"


def _parse_date(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _value(cell: Any) -> Any:
    if isinstance(cell, dict):
        return cell.get("value")
    if isinstance(cell, list):
        return [_value(c) for c in cell]
    return cell


def fetch_raw() -> dict[str, Any]:
    from mam.helpers.baserow import BaserowClient

    with BaserowClient() as client:
        media_fields = client.fields(client.media_table)
        return {
            "categories": client.category_terms(),
            "travel": client.travel_schedule(),
            "media": [
                {
                    "id": r["id"],
                    "date": r.get("date"),
                    "category": _value(r.get("category")),
                    "place": _value(r.get("place_location")),
                    "country": _value(r.get("country")),
                    "tag": r.get("tag"),
                    "title": r.get("title"),
                }
                for r in client.iter_rows(client.media_table)
            ],
            "options": {
                name: list(media_fields[name].options)
                for name in ("place_location", "country", "category", "language")
                if name in media_fields
            },
        }


def build_reference(raw: dict[str, Any], learned: dict[str, str] | None = None) -> Reference:
    aliases = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8"))
    aliases["places"] = {norm_key(k): v for k, v in (aliases.get("places") or {}).items()}
    aliases["categories"] = {norm_key(k): v for k, v in (aliases.get("categories") or {}).items()}
    aliases["languages"] = {norm_key(k): v for k, v in (aliases.get("languages") or {}).items()}
    for token, replacement in (learned or {}).items():
        aliases["places"][norm_key(token)] = replacement
    not_places = {norm_key(p) for p in aliases.get("not_places") or []}

    options = raw.get("options", {})
    country_options: dict[str, str] = {}
    for name in options.get("country", []):
        cc = cc_for_name(name)
        if cc:
            country_options.setdefault(cc, name)

    # Country of each place: most common country in the travel schedule, then the media table.
    place_country: dict[str, Counter] = defaultdict(Counter)
    stays: list[Stay] = []
    for row in raw.get("travel", []):
        start, end = _parse_date(row.get("start_date")), _parse_date(row.get("end_date"))
        place, country = (
            (row.get("place_location") or "").strip(),
            (row.get("country") or "").strip(),
        )
        if place and country:
            place_country[place][country] += 1
        if start and place:
            stays.append(Stay(start, end or start, place, country or None, cc_for_name(country)))
    media_by_date: dict[str, list[MediaRow]] = defaultdict(list)
    for row in raw.get("media", []):
        media = MediaRow(**row)
        if media.place and media.country:
            place_country[media.place][media.country] += 1
        if media.date:
            media_by_date[media.date[:10]].append(media)

    no_country = {norm_key(p) for p in aliases.get("no_country") or []}
    places: dict[str, Place] = {}
    names = set(options.get("place_location", [])) | set(place_country)
    built: list[Place] = []
    for name in sorted(n for n in names if isinstance(n, str)):
        key = norm_key(name)
        if key in not_places:
            continue
        country = place_country[name].most_common(1)[0][0] if place_country[name] else None
        if key in no_country:
            country = None
        place = Place(name, country, cc_for_name(country) if country else None)
        places.setdefault(key, place)  # exact names first ...
        built.append(place)
    for place in built:  # ... then the name without the ISKCON- prefix
        places.setdefault(norm_key(re.sub(r"^ISKCON-", "", place.name, flags=re.I)), place)
    exact = {norm_key(p.name): p for p in built}
    for key, target in aliases["places"].items():
        if key in exact:
            continue  # a real Baserow place ('Amsterdam') always beats an alias for it
        target_place = places.get(norm_key(target))
        if target_place:
            places[key] = target_place
    max_words = max((len(k.split()) for k in places), default=3)

    return Reference(
        matcher=CategoryMatcher(raw.get("categories", [])),
        places=places,
        stays=stays,
        media_by_date=dict(media_by_date),
        place_options=set(options.get("place_location", [])),
        country_options=country_options,
        category_options=set(options.get("category", [])),
        language_options=set(options.get("language", [])),
        aliases=aliases,
        max_place_words=min(max_words, 4),
    )


def learned_place_aliases() -> dict[str, str]:
    with session_scope() as session:
        rules = session.scalars(
            select(m.AliasRule).where(m.AliasRule.field == "where", m.AliasRule.active.is_(True))
        )
        return {r.token: r.replacement for r in rules}


def _fetch_and_store() -> Reference:
    """Fetch from Baserow (no lock held while waiting on the network) and update both caches."""
    global _cached
    raw = fetch_raw()
    cache = _cache_file()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    ref = build_reference(raw, learned_place_aliases())
    with _lock:
        _cached = ref
    return ref


def _from_disk() -> Reference | None:
    """The last Baserow data saved on disk (category colors, places, ...), instantly."""
    global _cached
    cache = _cache_file()
    if not cache.exists():
        return None
    try:
        ref = build_reference(
            json.loads(cache.read_text(encoding="utf-8")), learned_place_aliases()
        )
    except Exception:
        log.warning("Ignoring an unreadable reference cache %s", cache, exc_info=True)
        return None
    ref.loaded_at = cache.stat().st_mtime  # its real age decides when to refresh
    with _lock:
        if _cached is None:
            _cached = ref
    return _cached


def get_reference(refresh: bool = False) -> Reference:
    """Reference data from memory, else from the disk cache, else from Baserow.

    Data older than 10 minutes is still returned at once while a background refresh runs,
    so pages and jobs never wait for Baserow. `refresh=True` fetches now (e.g. after adding
    a place to the travel schedule).
    """
    if refresh:
        try:
            return _fetch_and_store()
        except Exception as exc:
            log.warning("Baserow unreachable (%s); using cached reference data", exc)
    ref = _cached or _from_disk()
    if ref is None:  # first run ever: nothing cached yet
        return _fetch_and_store()
    if time.time() - ref.loaded_at >= CACHE_TTL_S:
        preload_reference()
    return ref


def reference_if_ready() -> Reference | None:
    """Cached reference data (memory or disk) without ever waiting for Baserow."""
    ref = _cached or _from_disk()
    if ref is None or time.time() - ref.loaded_at >= CACHE_TTL_S:
        preload_reference()
    return ref


_preloading = threading.Event()


def preload_reference() -> None:
    """Refresh the reference data from Baserow in the background."""
    if _preloading.is_set():
        return
    _preloading.set()

    def load() -> None:
        try:
            _fetch_and_store()
        except Exception:
            log.warning("Could not refresh Baserow reference data in the background", exc_info=True)
        finally:
            _preloading.clear()

    threading.Thread(target=load, name="mam-reference", daemon=True).start()


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None
