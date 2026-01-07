from __future__ import annotations

from typer.testing import CliRunner

from wtfa.cli import app


runner = CliRunner()


def test_help_does_not_crash() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output


def test_rm_help_exposes_repo_option() -> None:
    result = runner.invoke(app, ["rm", "--help"])
    assert result.exit_code == 0, result.output
    assert "--repo" in result.output
