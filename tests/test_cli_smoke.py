from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.termui import strip_ansi
from typer.testing import CliRunner

from pal.cli import app


runner = CliRunner()


def test_help_does_not_crash() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output


def test_rm_help_exposes_repo_option() -> None:
    result = runner.invoke(app, ["rm", "--help"])
    assert result.exit_code == 0, result.output
    assert "--repo" in strip_ansi(result.output)


def test_run_codex_forwards_args_to_codex_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):  # noqa: ANN001
        assert check is True
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    # Patch the subprocess used by pal.codex.run_interactive
    monkeypatch.setattr(subprocess, "run", fake_run)

    # Create feature workspace dir so `pal run ... codex` passes validation.
    root = tmp_path / "projects"
    (root / "_wt" / "feat-auth").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "run",
            "feat-auth",
            "codex",
            "--root",
            str(root),
            "resume",
            "019b947b-ff0f-7ff3-8a49-4723ee751f20",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls, "expected codex subprocess.run to be invoked"
    assert "resume" in calls[0]


def test_plan_codex_injects_plan_slash_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):  # noqa: ANN001
        assert check is True
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    root = tmp_path / "projects"
    (root / "_wt" / "feat-auth").mkdir(parents=True)

    result = runner.invoke(app, ["plan", "feat-auth", "codex", "--root", str(root), "Review API"])
    assert result.exit_code == 0, result.output
    assert calls, "expected codex subprocess.run to be invoked"
    assert "/plan Review API" in calls[0]
