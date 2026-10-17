"""Typed settings loaded from .env (see .env.example)."""

from __future__ import annotations

import getpass
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOME = Path.home() / ".mam"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Baserow
    baserow_api_url: str = "https://api.baserow.io"
    baserow_api_token: str = ""
    baserow_database_id: int | None = None
    baserow_media_table_id: int | None = None
    baserow_travel_schedule_table_id: int | None = None
    baserow_category_terms_table_id: int | None = None
    # Baserow writes are dry-run unless this is true or a command passes --apply.
    baserow_apply: bool = False

    # Folders
    media_sample_files_path: Path = REPO_ROOT / "sample-files"
    media_processed_files_path: Path = REPO_ROOT / "processed-files"
    # Root of the mounted NAS archive. Originals under this path are never written to.
    nas_root: Path | None = None
    inbox_path: Path | None = None
    trimming_samples_path: Path = REPO_ROOT / "trimming-samples"
    transcriptions_path: Path = REPO_ROOT / "transcriptions"

    # Local state (never on the NAS share)
    local_db_path: Path = DEFAULT_HOME / "mam.sqlite"
    work_dir: Path = DEFAULT_HOME / "work"
    log_dir: Path = DEFAULT_HOME / "logs"

    operator_name: str = Field(default_factory=getpass.getuser)

    # Worker pools
    workers_ml: int = 1
    workers_cpu: int = 2
    workers_io: int = 4

    def ensure_dirs(self) -> None:
        for path in (self.local_db_path.parent, self.work_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def read_only_roots(self) -> list[Path]:
        """Folders holding originals; nothing may be written below them."""
        roots = [self.media_sample_files_path, self.trimming_samples_path]
        if self.nas_root:
            roots.append(self.nas_root)
        return [r.resolve() for r in roots]


@lru_cache
def get_settings() -> Settings:
    return Settings()
