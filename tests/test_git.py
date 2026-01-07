from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pal.git import worktree_add


def test_worktree_add_create_branch_uses_correct_git_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):  # noqa: ANN001
        assert check is True
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    repo_path = tmp_path / "repo"
    worktree_path = tmp_path / "wt" / "path"
    worktree_add(repo_path, worktree_path, "feat/x", create=True)

    assert len(calls) == 1
    cmd = calls[0]
    # Expected: git -C /repo worktree add -b feat/x /wt/path
    assert cmd[:5] == ["git", "-C", str(repo_path), "worktree", "add"]
    assert cmd[5:] == ["-b", "feat/x", str(worktree_path)]


def test_worktree_add_existing_branch_uses_correct_git_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):  # noqa: ANN001
        assert check is True
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    repo_path = tmp_path / "repo"
    worktree_path = tmp_path / "wt" / "path"
    worktree_add(repo_path, worktree_path, "feat/x", create=False)

    assert len(calls) == 1
    cmd = calls[0]
    # Expected: git -C /repo worktree add /wt/path feat/x
    assert cmd[:5] == ["git", "-C", str(repo_path), "worktree", "add"]
    assert cmd[5:] == [str(worktree_path), "feat/x"]
