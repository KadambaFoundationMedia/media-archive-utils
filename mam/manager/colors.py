"""Baserow select-option colors (category_terms_folder.color) as CSS colors.

Baserow names a color as an optional shade plus a hue: 'blue', 'light-blue', 'dark-blue',
'darker-blue'. The hues below approximate Baserow's palette; shades are mixed from them.
"""

from __future__ import annotations

from functools import lru_cache

HUES = {
    "blue": "#3d7fdb",
    "cyan": "#1ea7c5",
    "green": "#3a9d5d",
    "yellow": "#d9a514",
    "orange": "#e57b24",
    "red": "#d6453d",
    "brown": "#9a6a3a",
    "purple": "#8b5cd6",
    "pink": "#d0559a",
    "gray": "#7d8590",
}
SHADES = {"light": 0.55, "": 0.0, "dark": -0.25, "darker": -0.45}  # + towards white, - black


def _mix(hex_color: str, amount: float) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    target = 255 if amount > 0 else 0
    a = abs(amount)
    r, g, b = (round(c + (target - c) * a) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@lru_cache(maxsize=128)
def css_color(name: str | None) -> str | None:
    """'dark-blue' -> '#2e5fa4'; unknown names -> None."""
    if not name:
        return None
    shade, _, hue = name.strip().lower().rpartition("-")
    if hue not in HUES or shade not in SHADES:
        return None
    return _mix(HUES[hue], SHADES[shade])


@lru_cache(maxsize=128)
def badge_style(name: str | None) -> str:
    """Inline style for a category pill in the category's color, with readable text."""
    color = css_color(name)
    if color is None:
        return ""
    text = "#1f2328" if _luminance(color) > 0.55 else "#ffffff"
    return f"background-color:{color};color:{text};border-color:{color}"
