"""Engine, sessions and migrations for the local SQLite database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from mam.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_engines: dict[str, Engine] = {}


def _url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def get_engine(db_path: Path | None = None) -> Engine:
    path = Path(db_path or get_settings().local_db_path)
    key = str(path)
    if key not in _engines:
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(_url(path), connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")  # tools and the manager write concurrently
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        _engines[key] = engine
    return _engines[key]


def alembic_config(db_path: Path | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _url(Path(db_path or get_settings().local_db_path)))
    return cfg


def migrate(db_path: Path | None = None) -> None:
    """Create or upgrade the schema to the latest migration."""
    Path(db_path or get_settings().local_db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(db_path), "head")


@contextmanager
def session_scope(db_path: Path | None = None) -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error."""
    factory = sessionmaker(bind=get_engine(db_path), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
