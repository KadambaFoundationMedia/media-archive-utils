"""Options moved from .env to the Settings page."""

from __future__ import annotations

import pytest

from mam.helpers.local_db.app_settings import OPTION_BY_KEY, OPTIONS, parse_option


def test_every_option_names_a_settings_field():
    from mam.config import Settings

    for option in OPTIONS:
        assert option.key in Settings.model_fields


def test_values_are_checked():
    assert parse_option(OPTION_BY_KEY["spot_check_rate"], "0,1") == 0.1
    assert parse_option(OPTION_BY_KEY["workers_cpu"], "4") == 4
    assert parse_option(OPTION_BY_KEY["transcribe_words"], "on") is True
    assert parse_option(OPTION_BY_KEY["transcribe_languages"], "EN, nl") == "en,nl"
    for key, bad in (
        ("spot_check_rate", "2"),
        ("vedabase_delay_s", "5"),  # never faster than robots.txt allows
        ("workers_cpu", "x"),
        ("transcribe_languages", "english"),
        ("operator_name", " "),
    ):
        with pytest.raises(ValueError):
            parse_option(OPTION_BY_KEY[key], bad)
