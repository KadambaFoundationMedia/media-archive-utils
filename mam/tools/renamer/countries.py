"""Country names, ISO codes and ccTLDs (the WHERE country part of a filename)."""

from __future__ import annotations

import re
from functools import lru_cache

import pycountry

# ccTLDs that differ from the ISO 3166 alpha-2 code.
CCTLD_OVERRIDES = {"gb": "uk"}
# Names and abbreviations seen in the archive that pycountry does not resolve.
COUNTRY_ALIASES = {
    "emirates": "ae",
    "uae": "ae",
    "czech republic": "cz",
    "czech": "cz",
    "czechia": "cz",
    "cesko": "cz",
    "england": "uk",
    "great britain": "uk",
    "britain": "uk",
    "uk": "uk",
    "holland": "nl",
    "nederland": "nl",
    "deutschland": "de",
    "germany": "de",
    "sverige": "se",
    "norge": "no",
    "suomi": "fi",
    "slovenija": "si",
    "hrvatska": "hr",
    "srbija": "rs",
    "slovensko": "sk",
    "belgie": "be",
    "belgique": "be",
    "usa": "us",
    "america": "us",
    "south africa": "za",
    "russia": "ru",
}
# ISO 3166 alpha-3 codes accepted in filenames (others collide with ordinary words).
ALPHA3_ALLOWED = {
    "swe",
    "nld",
    "deu",
    "cze",
    "svk",
    "srb",
    "hrv",
    "svn",
    "bel",
    "fin",
    "nor",
    "gbr",
    "aut",
    "che",
    "ind",
    "zaf",
    "usa",
    "aus",
    "dnk",
    "fra",
    "ita",
    "esp",
    "hun",
    "rus",
    "bgr",
    "mus",
    "ken",
    "are",
    "pol",
    "irl",
    "nzl",
}


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().lower())


def to_cctld(alpha2: str) -> str:
    code = alpha2.lower()
    return CCTLD_OVERRIDES.get(code, code)


@lru_cache(maxsize=2048)
def cc_for_name(name: str) -> str | None:
    """ccTLD for a country name, alias or alpha-3 code ('Czech-republic' -> 'cz')."""
    key = _norm(name)
    if not key:
        return None
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    if len(key) == 3 and key in ALPHA3_ALLOWED:
        country = pycountry.countries.get(alpha_3=key.upper())
        return to_cctld(country.alpha_2) if country else None
    if len(key) <= 3:
        return None
    try:
        country = pycountry.countries.lookup(key)
    except LookupError:
        return None
    return to_cctld(country.alpha_2)


@lru_cache(maxsize=512)
def is_valid_cctld(code: str) -> bool:
    code = code.lower()
    if code == "uk":
        return True
    if len(code) != 2 or code == "gb":
        return False
    return pycountry.countries.get(alpha_2=code.upper()) is not None


@lru_cache(maxsize=512)
def country_name(cc: str) -> str | None:
    code = "gb" if cc.lower() == "uk" else cc.lower()
    country = pycountry.countries.get(alpha_2=code.upper())
    if country is None:
        return None
    return getattr(country, "common_name", None) or country.name
