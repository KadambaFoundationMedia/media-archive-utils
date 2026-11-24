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


def test_env_example_only_has_known_settings():
    """.env.example holds the private values and paths; every KEY= in it is a real setting.

    The other options are set on the Settings page, so they need not be listed here.
    """
    import re

    from mam.config import REPO_ROOT, Settings

    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))
    # Kept for tools that are not built yet (YouTube) and for the Baserow MCP connection.
    reserved = {"BASEROW_MCP_URL", "YOUTUBE_API_KEY_KF", "YOUTUBE_API_KEY_KKSBLOG"}
    known = {name.upper() for name in Settings.model_fields} | reserved
    assert "ANTHROPIC_API_KEY" in keys
    assert not keys - known, f"not a setting: {sorted(keys - known)}"
