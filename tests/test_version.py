from typer.testing import CliRunner

from mam import __version__
from mam.cli import app


def test_cli_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"mam {__version__}"


def test_changelog_mentions_current_version():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "CHANGELOG.md").read_text()
    assert f"## [{__version__}]" in text


def test_env_example_documents_every_setting():
    """Every setting the code reads is in .env.example (commented or not)."""
    from mam.config import REPO_ROOT, Settings

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").upper()
    missing = [name for name in Settings.model_fields if name.upper() not in text]
    assert not missing, f"add to .env.example: {missing}"
