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
