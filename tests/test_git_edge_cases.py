from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pal.git as git_module


def test_git_helpers_map_to_subprocess_and_path_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda exe: f"/bin/{exe}")

    assert git_module.exists_on_path("git") is True
    assert git_module.run(["ok"]) == "out"
    assert git_module.is_git_repo(tmp_path) is True
    assert git_module.branch_exists(tmp_path, "main") is True
    git_module.worktree_remove(tmp_path, tmp_path / "wt", force=False)
    assert run_calls[-1][-1] == str(tmp_path / "wt")
    git_module.worktree_remove(tmp_path, tmp_path / "wt", force=True)
    assert "-f" in run_calls[-1]
    assert git_module.git_status_short(tmp_path) == "out"
    assert git_module.git_porcelain(tmp_path) == "out"

    monkeypatch.setattr("shutil.which", lambda _exe: None)
    assert git_module.exists_on_path("missing") is False


def test_git_helpers_return_safe_defaults_when_commands_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(cmd, **_kwargs):  # noqa: ANN001
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(git_module.subprocess, "run", failing_run)

    assert git_module.is_git_repo(tmp_path) is False
    assert git_module.branch_exists(tmp_path, "main") is False
    assert git_module.git_diff_stat(tmp_path) == ""


def test_list_child_repos_skips_workspace_and_tooling_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_module, "is_git_repo", lambda p: p.name == "repo")
    for name in ["_wt", ".git", ".venv", "node_modules", "repo", "notrepo"]:
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / "file.txt").write_text("x\n", encoding="utf-8")

    assert git_module.list_child_repos(tmp_path) == ["repo"]
