from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mam.config import get_settings
from mam.helpers.local_db import db as local_db

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """Isolated settings: temp local DB, temp originals and processed folders, fake Baserow."""
    originals = tmp_path / "originals"
    processed = tmp_path / "processed"
    originals.mkdir()
    processed.mkdir()
    env = {
        "LOCAL_DB_PATH": str(tmp_path / "state" / "mam.sqlite"),
        "WORK_DIR": str(tmp_path / "work"),
        "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_SAMPLE_FILES_PATH": str(originals),
        "TRIMMING_SAMPLES_PATH": str(tmp_path / "trimming"),
        "MEDIA_PROCESSED_FILES_PATH": str(processed),
        "NAS_ROOT": "",
        "BASEROW_API_URL": "https://baserow.test",
        "BASEROW_API_TOKEN": "test-token",
        "BASEROW_MEDIA_TABLE_ID": "1",
        "BASEROW_TRAVEL_SCHEDULE_TABLE_ID": "2",
        "BASEROW_CATEGORY_TERMS_TABLE_ID": "3",
        "OPERATOR_NAME": "tester",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    local_db._engines.clear()

    def no_network():  # tests never reach Baserow; tests that need data use `reference`
        raise RuntimeError("no Baserow in tests")

    from mam.tools.renamer import knowledge

    monkeypatch.setattr(knowledge, "fetch_raw", no_network)
    s = get_settings()
    local_db.migrate()
    yield s
    for engine in local_db._engines.values():
        engine.dispose()
    local_db._engines.clear()
    get_settings.cache_clear()


@pytest.fixture
def category_rows():
    return json.loads((FIXTURES / "category_terms.json").read_text(encoding="utf-8"))


def make_tone(path: Path, *, seconds: float = 10, volume_db: float = -30, codec_args=None) -> Path:
    """Generate a short test clip with ffmpeg (no real media in the repo)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    codec_args = codec_args or ["-c:a", "libmp3lame", "-b:a", "128k"]
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={seconds}",
            "-af",
            f"volume={volume_db}dB",
            "-ac",
            "1",
            *codec_args,
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def tone(settings):
    return make_tone(settings.media_sample_files_path / "tone.mp3")


@pytest.fixture
def reference(monkeypatch):
    """Renamer reference data from tests/renamer_fixtures.py instead of Baserow."""
    import time

    from mam.tools.renamer import knowledge
    from tests.renamer_fixtures import reference_raw

    ref = knowledge.build_reference(reference_raw())
    ref.loaded_at = time.time() + 10_000
    monkeypatch.setattr(knowledge, "_cached", ref)
    return ref


@pytest.fixture
def no_baserow(monkeypatch):
    """Record Renamer Baserow writes instead of sending them (place_country writes are in
    `calls.place_country`)."""

    class Calls(list):
        place_country: list

    calls = Calls()
    calls.place_country = []

    def fake(record, proposal, final_path, settings, dry_run, live=False):
        calls.append({"file": record.id, "values": proposal, "path": str(final_path), "live": live})
        return {"action": "create", "row_id": None, "dry_run": True, "conflicts": {}}

    class FakeClient:
        """Records place_country writes (editing.remember_place_country)."""

        place_country_table = 1271584

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def create_row(self, table, values, **kwargs):
            calls.place_country.append({"action": "create", "values": values})
            return SimpleNamespace(row_id=99)

        def update_row(self, table, row_id, values, **kwargs):
            calls.place_country.append({"action": "update", "row_id": row_id, "values": values})
            return SimpleNamespace(row_id=row_id)

    monkeypatch.setattr("mam.tools.renamer.tool.write_baserow", fake)
    monkeypatch.setattr("mam.helpers.baserow.BaserowClient", FakeClient)
    return calls
