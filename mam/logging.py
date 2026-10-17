"""Logging: readable output on the console, JSON lines in the log file."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from rich.logging import RichHandler

from mam.config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if extra:
            entry["data"] = extra
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def setup_logging(verbose: bool = False) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)
    console = RichHandler(level=logging.DEBUG if verbose else logging.INFO, show_path=False)
    file = logging.FileHandler(settings.log_dir / "mam.log", encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(JsonFormatter())
    root.addHandler(console)
    root.addHandler(file)
    for noisy in ("httpx", "httpcore", "alembic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
