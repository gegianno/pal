from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pal.cli import app


runner = CliRunner()


def test_ls_lists_feature_dirs(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "_wt" / "email").mkdir(parents=True)

    result = runner.invoke(app, ["ls", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "email" in result.output


def test_repos_lists_child_repos_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Don't require git in tests; stub discovery.
    root = tmp_path / "projects"
    (root / "repo1").mkdir(parents=True)
    monkeypatch.setattr("pal.cli.list_child_repos", lambda _root: ["repo1"])

    result = runner.invoke(app, ["repos", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "repo1" in result.output


def test_open_writes_workspace_even_without_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    feature = "email"
    feature_dir = root / "_wt" / feature
    feature_dir.mkdir(parents=True)

    # Avoid calling git from VS Code workspace generation.
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda _p: False)
    # Make editor auto-detection consistently fail.
    monkeypatch.setattr("pal.cli.exists_on_path", lambda _exe: False)

    result = runner.invoke(app, ["open", feature, "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert (feature_dir / f"{feature}.code-workspace").exists()


def test_status_renders_repo_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    feature = "email"
    repo = "repo1"
    repo_dir = root / "_wt" / feature / repo
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr("pal.cli.is_git_repo", lambda _p: True)
    monkeypatch.setattr("pal.cli.git_status_short", lambda _p: "## feat/x")
    monkeypatch.setattr("pal.cli.git_porcelain", lambda _p: "")

    result = runner.invoke(app, ["status", feature, "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert repo in result.output
    assert "feat/x" in result.output


def test_new_creates_workspace_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    (root / "repo1").mkdir(parents=True)

    def fake_worktree_add(_repo_path, worktree_path, _branch, create):  # noqa: ANN001
        assert create is True
        worktree_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("pal.cli.is_git_repo", lambda _p: True)
    monkeypatch.setattr("pal.cli.branch_exists", lambda _repo_path, _branch: False)
    monkeypatch.setattr("pal.cli.worktree_add", fake_worktree_add)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda _p: True)

    feature = "feat-auth"
    result = runner.invoke(app, ["new", feature, "repo1", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "_wt" / feature / f"{feature}.code-workspace").exists()


def test_exec_forwards_prompt_to_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_run_exec(workspace_dir: Path, prompt: str, _cfg):  # noqa: ANN001
        calls.append((workspace_dir, prompt))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_exec", fake_run_exec)

    prompt = "Hello world"
    result = runner.invoke(app, ["exec", feature, prompt, "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert calls == [(root / "_wt" / feature, prompt)]
