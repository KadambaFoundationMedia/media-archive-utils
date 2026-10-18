"""Combine the evidence for one file into a rename proposal (tools/renamer/SPECS.md).

Evidence order: filename > parent folders > embedded tags > Baserow media table > travel
schedule. Each W is resolved on its own; the file is always renamed with what is known.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from mam.tools.renamer.countries import cc_for_name, country_name, is_valid_cctld
from mam.tools.renamer.dates import DateMatch, DateValue, find_dates
from mam.tools.renamer.knowledge import MediaRow, Place, Reference, norm_key
from mam.tools.renamer.naming import build_name, format_where
from mam.tools.renamer.parse import NameParser, ParsedName

log = logging.getLogger(__name__)

DATE_MARGIN = 0.25  # a date reading must beat the next one by this much to be chosen
# Matching an existing Baserow media row (date must match; these decide the rest).
MATCH_TAG = 0.6
MATCH_TITLE = 0.5  # times the share of title words in common
MATCH_PLACE = 0.2
MATCH_COUNTRY = 0.1
MATCH_CATEGORY = 0.1
MATCH_CONFLICT = 0.4  # a different verse, place or country
MATCH_THRESHOLD = 0.6
MATCH_MARGIN = 0.15
TITLE_STOPWORDS = {
    "a",
    "an",
    "the",
    "in",
    "on",
    "at",
    "of",
    "and",
    "with",
    "to",
    "for",
    "by",
    "from",
    "class",
    "lecture",
    "talk",
    "kks",
    "hh",
    "kadamba",
    "kanana",
    "swami",
    "maharaja",
}
TAG_AGREES = 1.0  # a filename date reading that the ID3 tags confirm (taken as correct)
FOLDER_LEVELS = 3


@dataclass
class Evidence:
    field: str  # when | who | what | where | category | language | baserow
    value: str
    source: str  # filename | folder | tag | siblings | media | travel | rule
    score: float
    selected: bool = False
    detail: dict[str, Any] | None = None


@dataclass
class Proposal:
    original_path: str
    ext: str
    when: str
    what: str | None
    place: str | None  # display form, e.g. 'Oslo'
    place_name: str | None  # Baserow place_location value when known
    cc: str | None
    where: str | None
    category: str | None
    verse_tag: str | None
    title: str
    languages: list[str]
    new_name: str
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    baserow_row_id: int | None = None
    new_place: dict[str, str] | None = None
    speakers: list[str] = field(default_factory=list)

    def to_dict(self, **extra: Any) -> dict[str, Any]:
        data = asdict(self)
        data.pop("evidence")
        data.update(extra)
        return data


@dataclass
class Context:
    folders: list[ParsedName]
    siblings: Counter  # (year, month) of dated sibling files
    sibling_places: dict[str, set[str]]  # full date -> known places named by siblings that day
    tag: ParsedName | None
    tag_year: int | None
    tag_origin: str | None = None  # e.g. 'ID3 tags: title "HH KKS BG2.54 11.07.2008"'
    tag_date_origin: str | None = None
    tag_dates: list = field(default_factory=list)  # (DateMatch, origin) from the ID3 date tag


# ------------------------------------------------------------------ context
def folder_chain(path: Path, roots: list[Path]) -> list[str]:
    """Parent folder names, nearest first, stopping at an import root."""
    names = []
    for parent in path.parents:
        if parent in roots or len(names) >= FOLDER_LEVELS or parent == parent.parent:
            break
        names.append(parent.name)
    return names


@lru_cache(maxsize=512)
def _sibling_names(folder: str) -> tuple[str, ...]:
    try:
        return tuple(sorted(n for n in os.listdir(folder) if not n.startswith(".")))
    except OSError:
        return ()


def read_tags(path: Path) -> dict[str, str]:
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if not audio or not audio.tags:
        return {}
    out = {}
    for key in ("title", "album", "date", "comment"):
        values = audio.tags.get(key)
        if values:
            out[key] = str(values[0]).strip()
    return out


RECORDER_ALBUMS = ("tascam", "zoom", "roland", "olympus", "sony", "dr-")


def build_context(path: Path, parser: NameParser, roots: list[Path]) -> Context:
    folders = [parser.parse(name, "folder") for name in folder_chain(path, roots)]
    siblings: Counter = Counter()
    sibling_places: dict[str, set[str]] = {}
    for name in _sibling_names(str(path.parent)):
        if name == path.name:
            continue
        parsed = parser.parse(Path(name).stem)
        for match in parsed.dates:
            if len(match.readings) == 1 and match.readings[0].value.is_full:
                v = match.readings[0].value
                siblings[(v.year, v.month)] += 1
                for place in parsed.places:
                    sibling_places.setdefault(v.text(), set()).add(place.name)
    tags = read_tags(path)
    useful = {
        k: v
        for k, v in tags.items()
        if k in ("title", "album")
        and norm_key(v) != norm_key(path.stem)
        and norm_key(v) != norm_key(path.name)
        and not norm_key(v).startswith(RECORDER_ALBUMS)
    }
    tag_text = " ".join(useful.values())
    tag_origin = (
        "ID3 tags: " + ", ".join(f'{k} "{v}"' for k, v in useful.items()) if useful else None
    )
    tag_year = None
    if tags.get("date", "")[:4].isdigit():
        tag_year = int(tags["date"][:4])
    tag = parser.parse(tag_text, "folder") if tag_text else None
    date_tag = tags.get("date", "")
    tag_dates = (
        [(d, f'"{d.raw}" in the ID3 date tag') for d in find_dates(date_tag)]
        if len(date_tag) > 4
        else []
    )
    context = Context(
        folders,
        siblings,
        sibling_places,
        tag,
        tag_year,
        tag_origin,
        f'ID3 date tag "{tags["date"]}"' if tag_year else None,
        tag_dates,
    )
    return context


# ------------------------------------------------------------------- resolver
def cc_for_name_safe(token: str) -> str | None:
    try:
        return cc_for_name(token)
    except Exception:
        return None


class Resolver:
    def __init__(self, ref: Reference, roots: list[Path]) -> None:
        self.ref = ref
        self.parser = NameParser(ref)
        self.roots = [r.resolve() for r in roots]

    def propose(self, path: Path) -> Proposal:
        path = Path(path)
        name = self.parser.parse(path.stem)
        ctx = build_context(path, self.parser, self.roots)
        ev: list[Evidence] = []
        flags: list[str] = []
        notes: list[str] = []

        place, place_text, cc = self._where(name, ctx, None, ev, flags, notes, first_pass=True)
        when = self._when(name, ctx, place, cc, ev, flags, notes)
        media_row = self._media_match(when, name, place, cc, ev, notes, flags)
        place, place_text, cc = self._where(name, ctx, when, ev, flags, notes, media_row=media_row)

        what = self._what(name)
        if what:
            ev.append(Evidence("what", what, "filename", 0.9, True, {"origin": f'"{path.stem}"'}))
        if not name.descriptive:
            flags.append(
                "Non-descriptive filename: it gives no WHAT (scripture, verse or title)."
                + (" It looks like a recorder file name." if name.recorder else "")
            )
        category = self._category(what, name, ctx, media_row, ev)
        languages = self._languages(name, ctx)
        speakers = list(dict.fromkeys(name.speakers))
        if speakers and not name.has_kks:
            flags.append(
                f"Another speaker in the filename ({', '.join(speakers)}). WHO is always KKS; "
                "check that this is a recording of Kadamba Kanana Swami."
            )
        elif speakers:
            notes.append(f"The filename also names {', '.join(speakers)}.")

        where = format_where(place_text, cc)
        new_place = None
        if place_text and not place:
            suggestion = self._closest_place(place_text)
            flags.append(
                f"New place '{place_text}' is not in Baserow. Add it to the travel schedule, "
                "or correct it" + (f" (did you mean {suggestion}?)." if suggestion else ".")
            )
            new_place = {"place": place_text, "country": country_name(cc) if cc else ""}
        if cc and is_valid_cctld(cc) and cc not in self.ref.country_options:
            flags.append(f"New country '{country_name(cc)}' ({cc}) is not an option in Baserow.")

        new_name = build_name(when, what, where, path.suffix)
        title = self._title(name, ctx, path)
        return Proposal(
            original_path=str(path),
            ext=path.suffix.lower().lstrip("."),
            when=when.text(),
            what=what,
            place=place_text,
            place_name=place.name if place else None,
            cc=cc,
            where=where,
            category=category,
            verse_tag=name.verse.tag if name.verse and name.verse.numbers else None,
            title=title,
            languages=languages,
            new_name=new_name,
            flags=flags,
            notes=notes,
            evidence=ev,
            baserow_row_id=media_row.id if media_row else None,
            new_place=new_place,
            speakers=speakers,
        )

    # ------------------------------------------------------------- WHEN
    def _when(self, name, ctx, place, cc, ev, flags, notes) -> DateValue:
        cands: list[tuple[DateValue, float, str, DateMatch | None, str]] = []
        for match in name.dates:
            for r in match.readings:
                cands.append(
                    (r.value, r.prior, "filename", match, f'"{match.raw}" in the filename')
                )
        # Dates in the ID3 title or date tag are taken as correct unless other evidence conflicts.
        tag_cands = []
        tag_matches = [
            (d, f'"{d.raw}" in the {ctx.tag_origin}') for d in (ctx.tag.dates if ctx.tag else [])
        ]
        for match, origin in tag_matches + ctx.tag_dates:
            for r in match.readings:
                tag_cands.append((r.value, r.prior * 0.9, "tag", match, origin))
        tag_full = {c[0] for c in tag_cands if c[0].is_full}
        if cands and tag_full:
            confirmed = [c for c in cands if c[0] in tag_full]
            if confirmed:
                cands = [
                    (v, base + (TAG_AGREES if v in tag_full else 0.0), s, mt, o)
                    for v, base, s, mt, o in cands
                ]
                notes.append(f"The ID3 tags confirm the date ({confirmed[0][0].text()}).")
        source_weight = {"folder": 0.6}
        if not cands and tag_cands:
            cands = list(tag_cands)
        if not cands:
            for folder in ctx.folders:
                for match in folder.dates:
                    origin = f'"{match.raw}" in folder "{folder.text}"'
                    for r in match.readings:
                        weight = r.prior * source_weight["folder"]
                        cands.append((r.value, weight, "folder", match, origin))
                if cands:
                    break  # nearest folder with a date wins
        if not cands and ctx.tag_year:
            cands.append((DateValue(ctx.tag_year), 0.4, "tag", None, ctx.tag_date_origin))
        if not cands:
            ev.append(
                Evidence("when", "YYYY-MM-DD", "rule", 0.0, True, {"origin": "no date found"})
            )
            return DateValue()

        folder_values = [r.value for f in ctx.folders for d in f.dates for r in d.readings]
        scored = []
        for value, base, source, match, origin in cands:
            context = (
                self._date_context(value, folder_values, ctx, place, cc)
                if source in ("filename", "tag")
                else 0.0
            )
            specificity = 0.1 * sum(x is not None for x in (value.year, value.month, value.day))
            scored.append((base + context + specificity, value, source, match, origin))
        scored.sort(key=lambda s: s[0], reverse=True)
        for score, value, source, match, origin in scored:
            fmt = next((r.fmt for r in match.readings if r.value == value), None) if match else None
            detail = {"origin": origin + (f" (read as {fmt})" if fmt and match.ambiguous else "")}
            ev.append(Evidence("when", value.text(), source, round(score, 2), detail=detail))

        best_score, best, source, match, _ = scored[0]
        rivals = [s for s in scored[1:] if s[1] != best and s[1].is_full and best.is_full]
        if rivals and best_score - rivals[0][0] < DATE_MARGIN:
            options = [best] + [r[1] for r in rivals if best_score - r[0] < DATE_MARGIN]
            common = DateValue(
                *(
                    parts[0] if len(set(parts)) == 1 else None
                    for parts in zip(*((o.year, o.month, o.day) for o in options), strict=True)
                )
            )
            flags.append(
                f"Date format cannot be determined: '{match.raw if match else ''}' could be "
                + " or ".join(o.text() for o in options)
                + ". Not resolved by folder, travel schedule or media table."
            )
            self._select(
                ev, "when", common.text(), "rule", "only the date parts all readings agree on"
            )
            return common

        if source == "folder":
            notes.append(f"Date taken from the folder name ('{match.raw}').")
        elif source == "tag":
            notes.append(
                f"Date taken from the ID3 tags ('{match.raw if match else ctx.tag_year}')."
            )
            folder_years = {v.year for v in folder_values if v.year}
            if best.year and folder_years and best.year not in folder_years:
                flags.append(
                    f"The ID3 date {best.text()} conflicts with the folder name "
                    f"(year {', '.join(str(y) for y in sorted(folder_years))})."
                )
        elif match and match.ambiguous:
            fmt = next(r.fmt for r in match.readings if r.value == best)
            notes.append(
                f"Date '{match.raw}' read as {fmt} ({best.text()}) using folder, siblings, "
                "travel schedule or media evidence."
            )
        if match and match.note:
            notes.append(match.note)
        # Fill missing parts from an agreeing, more complete folder date.
        if not best.is_full and source == "filename":
            fuller = {v for v in folder_values if v.agrees_with(best) and v.is_full}
            if len(fuller) == 1:
                best = fuller.pop()
                notes.append(f"Missing date parts filled from the folder name ({best.text()}).")
        self._select(ev, "when", best.text())
        return best

    def _date_context(self, value: DateValue, folder_values, ctx: Context, place, cc) -> float:
        score = 0.0
        if value.year:
            years = {v.year for v in folder_values if v.year}
            if value.year in years:
                score += 0.3
                if value.month and value.month in {
                    v.month for v in folder_values if v.year == value.year
                }:
                    score += 0.2
            elif years:
                score -= 0.2
            if (value.year, value.month) in ctx.siblings:
                score += 0.6
            elif any(y == value.year for y, _ in ctx.siblings):
                score += 0.15
        d = value.as_date()
        if d:
            stays = self.ref.stays_on(d)
            if place and any(s.place == place.name for s in stays):
                score += 0.5
            elif cc and any(s.cc == cc for s in stays):
                score += 0.35
        return score

    # ------------------------------------------------------------ WHERE
    def _where(self, name, ctx, when, ev, flags, notes, first_pass=False, media_row=None):
        """Returns (known Place or None, place text for the filename, cc)."""
        place: Place | None = name.places[0] if name.places else None
        place_text = place.display if place else name.unknown_place
        source = "filename" if place_text else None
        origin = f'"{place_text}" in the filename' if place_text else None
        if place_text is None and when is not None and when.is_full:
            same_day = ctx.sibling_places.get(when.text(), set())
            if len(same_day) == 1:
                place = self.ref.find_place(next(iter(same_day)))
                if place:
                    place_text, source = place.display, "siblings"
                    origin = f"other files in this folder recorded on {when.text()}"
        if place_text is None:
            folder_places = {p.name: (p, f.text) for f in ctx.folders for p in f.places}
            if len(folder_places) == 1:
                place, folder_text = next(iter(folder_places.values()))
                place_text, source = place.display, "folder"
                origin = f'folder "{folder_text}"'
        if place_text is None and media_row and media_row.place:
            place = self.ref.find_place(media_row.place)
            place_text, source = (place.display if place else media_row.place), "media"
            origin = f"Baserow media row {media_row.id}"
        if place_text is None and when is not None and when.is_full:
            named_cc = name.ccs[0][0] if name.ccs else None
            stays = {
                s.place
                for s in self.ref.stays_on(when.as_date())
                if named_cc is None or s.cc == named_cc  # never contradict the filename
            }
            if len(stays) == 1:
                place = self.ref.find_place(stays.pop())
                if place:
                    place_text, source = place.display, "travel"
                    origin = f"travel schedule on {when.text()}"
        if first_pass:
            cc = name.ccs[0][0] if name.ccs else (place.cc if place else None)
            return place, place_text, cc

        cc = self._country(name, ctx, place, when, media_row, notes, flags, ev)
        if when is not None and when.is_full and source != "travel":
            self._travel_evidence(when, place_text, ev, notes)
        if place_text:
            ev.append(
                Evidence(
                    "where",
                    place_text,
                    source,
                    {"filename": 0.9, "siblings": 0.7, "folder": 0.6, "media": 0.6, "travel": 0.5}[
                        source
                    ],
                    True,
                    {"origin": origin},
                )
            )
            if source == "travel":
                notes.append(f"Place from the travel schedule ({place.name}); not proof.")
            elif source == "siblings":
                notes.append(f"Place from other files recorded the same day ({place.name}).")
        if when is not None and when.is_full and place and source == "filename":
            stays = self.ref.stays_on(when.as_date())
            if stays and not any(s.place == place.name or (s.cc and s.cc == cc) for s in stays):
                notes.append(
                    "Travel schedule has KKS in "
                    + ", ".join(sorted({s.place for s in stays}))
                    + f" on {when.text()}."
                )
        return place, place_text, cc

    def _travel_evidence(self, when: DateValue, place_text, ev, notes) -> None:
        """Show every stay on the date, even when it was not used for the place."""
        d = when.as_date()
        stays = self.ref.stays_on(d)
        for s in stays:
            origin = (
                f"travel schedule: {s.place}, {s.country}, {s.start:%Y-%m-%d} to {s.end:%Y-%m-%d}"
            )
            ev.append(Evidence("where", s.place, "travel", 0.5, False, {"origin": origin}))
        if place_text is None and len({s.place for s in stays}) > 1:
            parts = []
            for s in sorted(stays, key=lambda s: s.start):
                if s.end == d and s.start != d:
                    parts.append(f"leaves {s.place}")
                elif s.start == d and s.end != d:
                    parts.append(f"arrives in {s.place}")
                else:
                    parts.append(f"is in {s.place}")
            notes.append(
                f"Travel day on {when.text()}: KKS "
                + " and ".join(parts)
                + ". The place is left open; choose it in the review."
            )

    def _country(self, name, ctx, place, when, media_row, notes, flags, ev) -> str | None:
        explicit = [(cc, raw) for cc, raw in name.ccs]
        place_cc = place.cc if place else None
        for cc, raw in explicit:
            ev.append(
                Evidence(
                    "where", cc, "filename", 0.9, detail={"origin": f'"{raw}" in the filename'}
                )
            )
        if place_cc:
            origin = f"{place.name} is in {place.country} (Baserow places)"
            ev.append(Evidence("where", place_cc, "place", 0.85, detail={"origin": origin}))
        if explicit:
            chosen, raw = explicit[0]
            visited = set(self.ref.country_options)
            others = [c for c, _ in explicit[1:] if c != chosen] + (
                [place_cc] if place_cc and place_cc != chosen else []
            )
            if not is_valid_cctld(chosen) or chosen not in visited:
                valid_others = list(dict.fromkeys(c for c in others if c in visited))
                if len(valid_others) == 1:
                    notes.append(
                        f"Corrected country code '{raw}' to '{valid_others[0]}' "
                        f"({country_name(valid_others[0])})."
                    )
                    return valid_others[0]
                if not is_valid_cctld(chosen):
                    flags.append(f"Unknown country code '{raw}'.")
                    return None
            if place_cc and place_cc != chosen:
                flags.append(
                    f"Country code '{chosen}' does not match {place.name} "
                    f"({place.country}, '{place_cc}'). Check the place or the country."
                )
            return chosen
        if place_cc:
            return place_cc
        folder_ccs = {cc: f.text for f in ctx.folders for cc, _ in f.ccs} | {
            p.cc: f.text for f in ctx.folders for p in f.places if p.cc
        }
        if len(folder_ccs) == 1:
            cc, folder_text = next(iter(folder_ccs.items()))
            detail = {"origin": f'folder "{folder_text}"'}
            ev.append(Evidence("where", cc, "folder", 0.6, detail=detail))
            return cc
        if media_row and media_row.country:
            from mam.tools.renamer.countries import cc_for_name

            return cc_for_name(media_row.country)
        if when is not None and when.is_full:
            ccs = {s.cc for s in self.ref.stays_on(when.as_date()) if s.cc}
            if len(ccs) == 1:
                detail = {"origin": f"travel schedule on {when.text()}"}
                ev.append(Evidence("where", next(iter(ccs)), "travel", 0.5, detail=detail))
                notes.append("Country from the travel schedule; not proof.")
                return ccs.pop()
        return None

    # ------------------------------------------------------- WHAT & more
    @staticmethod
    def _what(name: ParsedName) -> str | None:
        parts = [name.verse.code] if name.verse else []
        parts += name.what_words
        return "-".join(parts) or None

    def _category(self, what, name, ctx, media_row, ev) -> str | None:
        found = origin = None
        match = self.ref.matcher.match(what)
        if match:
            found, source = match.category.name, "filename"
            origin = f'"{match.term}" in the WHAT (Baserow category terms)'
        elif name.categories:
            found, source, origin = (
                name.categories[0],
                "filename",
                "a category word in the filename",
            )
        else:
            for folder in ctx.folders:
                hint = folder.categories[0] if folder.categories else None
                if not hint:
                    folder_match = self.ref.matcher.match(folder.text)
                    hint = folder_match.category.name if folder_match else None
                if hint:
                    found, source, origin = hint, "folder", f'folder "{folder.text}"'
                    break
        if found is None and media_row and media_row.category:
            found, source = media_row.category, "media"
            origin = f"Baserow media row {media_row.id}"
        if found:
            ev.append(Evidence("category", found, source, 0.8, True, {"origin": origin}))
        return found

    @staticmethod
    def _languages(name, ctx) -> list[str]:
        langs = ["English"] + name.languages + [lang for f in ctx.folders for lang in f.languages]
        return list(dict.fromkeys(langs))

    @staticmethod
    def _title(name: ParsedName, ctx: Context, path: Path) -> str:
        if name.what_words:
            words = " ".join(name.what_words)
            return f"{name.verse.readable} {words}" if name.verse else words
        if name.verse:
            return name.verse.readable
        for folder in ctx.folders:
            if folder.what_words:
                return " ".join(folder.what_words)
        return path.stem

    def _closest_place(self, text: str) -> str | None:
        import difflib

        found = difflib.get_close_matches(norm_key(text), list(self.ref.places), n=1, cutoff=0.75)
        return self.ref.places[found[0]].name if found else None

    def _media_match(self, when: DateValue, name, place, cc, ev, notes, flags) -> MediaRow | None:
        """An existing Baserow media row for this recording, or None (a new row is created).

        The date alone never decides: a row must also agree on the verse tag and/or the title,
        and a different verse, place or country counts against it.
        """
        if not when.is_full:
            return None
        rows = self.ref.media_by_date.get(when.text(), [])
        if not rows:
            return None
        what = self._what(name)
        match = self.ref.matcher.match(what)
        category = match.category.name if match else None
        tag = name.verse.tag if name.verse and name.verse.numbers else None
        ours = self._title_tokens(name.what_words + ([name.verse.readable] if name.verse else []))

        def score(row: MediaRow) -> tuple[float, list[str]]:
            s, why = 0.0, []
            if tag and row.tag:
                if row.tag.strip() == tag:
                    s, why = s + MATCH_TAG, [*why, "same verse tag"]
                else:
                    s, why = s - MATCH_CONFLICT, [*why, f"different verse ({row.tag})"]
            theirs = self._title_tokens([row.title or ""])
            if ours and theirs:
                overlap = len(ours & theirs) / len(ours | theirs)
                if overlap:
                    s += MATCH_TITLE * overlap
                    why.append(f"title overlap {overlap:.0%}")
            online = {norm_key(x) for x in self.ref.aliases.get("no_country") or []}
            is_online = place and (
                norm_key(place.name) in online or norm_key(row.place or "") in online
            )
            if place and row.place and is_online:
                pass  # a livestream happens somewhere: neither proof nor conflict
            elif place and row.place:
                if row.place == place.name:
                    s, why = s + MATCH_PLACE, [*why, "same place"]
                else:
                    s, why = s - MATCH_CONFLICT, [*why, f"different place ({row.place})"]
            elif cc and row.country:
                if cc_for_name(row.country) == cc:
                    s, why = s + MATCH_COUNTRY, [*why, "same country"]
                else:
                    s, why = s - MATCH_CONFLICT, [*why, f"different country ({row.country})"]
            if category and row.category == category:
                s, why = s + MATCH_CATEGORY, [*why, "same category"]
            return s, why

        ranked = sorted(((*score(r), r) for r in rows), key=lambda x: x[0], reverse=True)
        best_score, why, best = ranked[0]
        has_identity = any(w.startswith(("same verse", "title overlap")) for w in why)
        clear_winner = len(ranked) == 1 or ranked[1][0] <= best_score - MATCH_MARGIN
        if best_score >= MATCH_THRESHOLD and has_identity and clear_winner:
            ev.append(
                Evidence(
                    "baserow",
                    f"row {best.id}: {best.title}",
                    "media",
                    round(min(best_score, 1.0), 2),
                    True,
                    {"origin": "same date, " + ", ".join(why)},
                )
            )
            notes.append(f"Matches Baserow media row {best.id} ('{best.title}').")
            return best
        close = [
            r
            for sc, w, r in ranked
            if sc >= MATCH_THRESHOLD
            and any(x.startswith(("same verse", "title overlap")) for x in w)
        ]
        if len(close) > 1:
            flags.append(
                "Several Baserow rows may be this recording ("
                + ", ".join(f"row {r.id} '{(r.title or '')[:40]}'" for r in close[:4])
                + "). Choose the right row in the review, or create a new one."
            )
            return None
        notes.append(
            f"{len(rows)} Baserow media row(s) on {when.text()}, none clearly the same "
            "recording (title, tag and place compared); a new row will be created."
        )
        return None

    def _title_tokens(self, texts: list[str]) -> set[str]:
        """Meaningful words of a title: no stop words, places or country names."""
        tokens = set()
        for text in texts:
            for token in norm_key(text).split():
                if token in TITLE_STOPWORDS or token in self.ref.places:
                    continue
                if len(token) > 3 and cc_for_name_safe(token):
                    continue
                tokens.add(token)
        return tokens

    @staticmethod
    def _select(
        ev: list[Evidence],
        field_name: str,
        value: str,
        add_source: str | None = None,
        origin: str | None = None,
    ) -> None:
        for e in ev:
            if e.field == field_name and e.value == value:
                e.selected = True
                return
        detail = {"origin": origin} if origin else None
        ev.append(Evidence(field_name, value, add_source or "rule", 0.0, True, detail))
