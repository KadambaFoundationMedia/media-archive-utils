"""One-time sync of the Baserow place_country table (one row per media place_location).

The country of each place is chosen from, in order:
1. the media rows with that place, when they agree on one country;
2. the travel schedule, only as a hint where no media row has a country;
3. a short list of places with no data (GEOGRAPHY below).
General places (aliases.yaml `no_country`, e.g. Zoom-livestream) get an empty country, and
spelling variants of a place (same name apart from case, or `SPELLING_VARIANTS`) get no row.

The media table is tidied at the same time (fill-empty only, plus one spelling fix):
- a media row with a place and no country gets the place's country;
- a country option spelled two ways (`Czech-republic` / `Czech-Republic`) is set to the
  spelling most rows use.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from mam.tools.renamer.countries import cc_for_name
from mam.tools.renamer.knowledge import norm_key

# Places without media rows or travel rows with a country (checked by hand, 2026-10-20).
GEOGRAPHY = {
    "Amsterdam": "nl",
    "Netherlands": "nl",
    "Amritsar": "in",
    "Ucha-Gaon": "in",
    "Bhakti-Center-NYC": "us",
    "Farma-Krishna-Dvur": "cz",
    "Pruhon": "cz",
    "ISKCON-Malaga": "es",
    "Surfers-Paradise": "au",
}
# Other spellings of a place option; they get no row (merge them in Baserow).
SPELLING_VARIANTS = {"Sumercamp": "Summercamp"}


@dataclass
class PlaceRow:
    place: str
    country: str  # media country option, '' for a general place
    cc: str
    source: str  # media | travel | geography | general | none
    row_id: int | None = None  # existing place_country row to fill
    action: str = "create"  # create | update | keep


@dataclass
class MediaFix:
    row_id: int
    place: str | None
    old: str | None
    new: str
    reason: str


@dataclass
class SyncPlan:
    rows: list[PlaceRow] = field(default_factory=list)
    media: list[MediaFix] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # spelling variants
    unknown: list[str] = field(default_factory=list)  # places with no country found


def plan_sync(raw: dict[str, Any], no_country: list[str]) -> SyncPlan:
    plan = SyncPlan()
    countries = raw["options"].get("country", [])
    # one spelling per country code: the one most media rows use
    used = Counter(r["country"] for r in raw["media"] if r.get("country"))
    by_cc: dict[str, str] = {}
    for name in sorted(countries, key=lambda n: (-used[n], n)):
        cc = cc_for_name(name)
        if cc:
            by_cc.setdefault(cc, name)

    media_cc: dict[str, Counter] = defaultdict(Counter)
    for r in raw["media"]:
        cc = cc_for_name(r["country"]) if r.get("country") else None
        if r.get("place") and cc:
            media_cc[r["place"]][cc] += 1
    travel_cc: dict[str, Counter] = defaultdict(Counter)
    for r in raw["travel"]:
        place = (r.get("place_location") or "").strip()
        cc = cc_for_name((r.get("country") or "").strip())
        if place and cc:
            travel_cc[place][cc] += 1

    general = {norm_key(p) for p in no_country}
    existing = {norm_key(r["place"]): r for r in raw.get("place_country", []) if r.get("place")}
    empty_rows = [r["id"] for r in raw.get("place_country", []) if not r.get("place")]
    seen: set[str] = set()
    for place in sorted(raw["options"].get("place_location", [])):
        key = norm_key(place)
        if key in seen or place in SPELLING_VARIANTS:
            plan.skipped.append(place)
            continue
        seen.add(key)
        if key in general:
            cc, source = "", "general"
        elif len(media_cc[place]) == 1:
            cc, source = next(iter(media_cc[place])), "media"
        elif media_cc[place]:
            cc, source = "", "general"  # media rows disagree: a general place
        elif len(travel_cc[place]) >= 1:
            cc, source = travel_cc[place].most_common(1)[0][0], "travel"
        elif place in GEOGRAPHY:
            cc, source = GEOGRAPHY[place], "geography"
        else:
            cc, source = "", "none"
            plan.unknown.append(place)
        row = PlaceRow(place, by_cc.get(cc, "") if cc else "", cc, source)
        old = existing.get(key)
        if old:
            row.row_id = old["id"]
            row.action = "keep" if old.get("country") or not cc else "update"
            if row.action == "keep":
                row.country, row.cc = old.get("country") or "", old.get("cc") or ""
        elif empty_rows:
            row.row_id, row.action = empty_rows.pop(0), "update"
        plan.rows.append(row)

    country_of = {norm_key(r.place): r for r in plan.rows if r.cc}
    for r in raw["media"]:
        cc = cc_for_name(r["country"]) if r.get("country") else None
        if cc and by_cc.get(cc) and r["country"] != by_cc[cc]:
            plan.media.append(
                MediaFix(r["id"], r.get("place"), r["country"], by_cc[cc], "country spelling")
            )
        elif r.get("place") and not r.get("country"):
            known = country_of.get(norm_key(r["place"]))
            if known and known.country:
                plan.media.append(
                    MediaFix(r["id"], r["place"], None, known.country, "country from place")
                )
    return plan


def apply_sync(plan: SyncPlan, client, dry_run: bool) -> dict[str, int]:
    """Write the plan through BaserowClient (write log + undo). Returns counts per action."""
    done: Counter = Counter()
    table = client.place_country_table
    for row in plan.rows:
        if row.action == "keep":
            continue
        values = {"place_location": row.place, "country": row.country, "ccTLD": row.cc}
        if row.row_id:
            client.update_row(
                table,
                row.row_id,
                values,
                tool="place-country-sync",
                dry_run=dry_run,
                merge=False,
            )
        else:
            client.create_row(table, values, tool="place-country-sync", dry_run=dry_run)
        done[row.action] += 1
    for fix in plan.media:
        client.update_row(
            client.media_table,
            fix.row_id,
            {"country": fix.new},
            tool="place-country-sync",
            dry_run=dry_run,
            overwrite={"country"} if fix.old else frozenset(),
        )
        done["media"] += 1
    return dict(done)
