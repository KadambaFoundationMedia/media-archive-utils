"""Detects that the program files changed after the portal started.

Python does not reload modules: a portal that was running during a `git pull` keeps the old code
in memory and mixes it with the new files (an ImportError on the next job). The portal shows a
banner asking for a restart instead.
"""

from __future__ import annotations

import time
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
_CACHE_S = 5.0


def newest_change() -> float:
    return max((p.stat().st_mtime for p in PACKAGE.rglob("*.py")), default=0.0)


STARTED_CODE = newest_change()
_checked = [0.0, False]


def code_changed() -> bool:
    """True when a program file is newer than the code this process loaded."""
    now = time.monotonic()
    if now - _checked[0] > _CACHE_S:
        _checked[0] = now
        try:
            _checked[1] = newest_change() > STARTED_CODE + 1.0
        except OSError:
            _checked[1] = False
    return _checked[1]
