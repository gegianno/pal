from __future__ import annotations

from pathlib import Path

import pytest

from pal.config import LocalFilesRepoConfig, PalConfig
from pal.local_files import CopyResult
from pal.workspaces import (
    STATE_ONLY_WORKSPACE_MODE,
    WorkspaceLocalFilesResult,
    WorkspacePrepareResult,
    WorkspaceRepoResult,
    validate_workspace_mode,
)
import pal.workspaces.git_worktree as git_worktree
from pal.workspaces.git_worktree import GitWorktreeWorkspaceBackend


def _cfg(root: Path) -> PalConfig:
    return PalConfig(root=root, worktree_root=root / "_wt")


def test_workspace_mode_validation_and_result_serialization(tmp_path: Path) -> None:
    assert validate_workspace_mode(" ReUse ") == "reuse"
    assert validate_workspace_mode(STATE_ONLY_WORKSPACE_MODE) == STATE_ONLY_WORKSPACE_MODE

    with pytest.raises(ValueError, match="Unknown workspace mode"):
        validate_workspace_mode("remote")

    local_files = WorkspaceLocalFilesResult(
        copied=[".env"],
        skipped_missing=["missing.env"],
        skipped_existing=[".env.local"],
        skipped_invalid=["../bad"],
    )
    repo = WorkspaceRepoResult(
        repo="api",
        source_path=str(tmp_path / "api"),
        worktree_path=str(tmp_path / "_wt" / "feat" / "api"),
        branch="feat/feat",
        status="created",
        local_files=local_files,
    )
    result = WorkspacePrepareResult(
        feature="feat",
        mode="create",
        workspace_dir=str(tmp_path / "_wt" / "feat"),
        workspace_file=str(tmp_path / "_wt" / "feat" / "feat.code-workspace"),
        repos=[repo],
    )

    assert result.to_dict()["repos"][0]["local_files"] == local_files.to_dict()


def test_git_worktree_backend_state_only_does_not_touch_files(tmp_path: Path) -> None:
    backend = GitWorktreeWorkspaceBackend(_cfg(tmp_path))

    result = backend.prepare(feature="feat", repos=["api"], mode="state-only")

    assert result.mode == "state-only"
    assert result.workspace_dir == str(tmp_path / "_wt" / "feat")
    assert result.workspace_file == ""
    assert result.repos == []
    assert not (tmp_path / "_wt").exists()


def test_git_worktree_backend_creates_reuses_and_copies_local_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    cfg.local_files.enabled = True
    cfg.local_files.paths = [".env"]
    cfg.local_files.patterns = ["*.secret"]
    cfg.local_files.repos["api"] = LocalFilesRepoConfig(
        paths=["api.env"],
        patterns=["api.*"],
    )
    for repo in ("api", "web", "docs"):
        (tmp_path / repo).mkdir()
    (tmp_path / "_wt" / "feat" / "docs").mkdir(parents=True)

    git_repos = {tmp_path / repo for repo in ("api", "web", "docs")}
    git_repos.add(tmp_path / "_wt" / "feat" / "docs")
    monkeypatch.setattr(git_worktree, "is_git_repo", lambda path: path in git_repos)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda path: path.name in {"api", "web", "docs"})
    monkeypatch.setattr(
        git_worktree,
        "branch_exists",
        lambda repo_path, _branch: repo_path.name == "web",
    )
    worktree_calls: list[tuple[Path, Path, str, bool]] = []

    def fake_worktree_add(repo_path: Path, worktree_path: Path, branch: str, create: bool) -> None:
        worktree_calls.append((repo_path, worktree_path, branch, create))
        worktree_path.mkdir(parents=True)
        git_repos.add(worktree_path)

    resolve_calls: list[tuple[Path, list[str], list[str]]] = []
    copy_calls: list[tuple[Path, Path, list[str], bool]] = []

    def fake_resolve(repo_path: Path, *, paths: list[str], patterns: list[str]):
        resolve_calls.append((repo_path, paths, patterns))
        return ["resolved.env"], ["../bad"]

    def fake_copy(
        source_repo_dir: Path,
        dest_worktree_dir: Path,
        *,
        paths: list[str],
        overwrite: bool,
    ) -> CopyResult:
        copy_calls.append((source_repo_dir, dest_worktree_dir, paths, overwrite))
        return CopyResult(
            copied=[Path("resolved.env")],
            skipped_missing=[Path("missing.env")],
            skipped_existing=[Path(".env.local")],
            skipped_invalid=["/abs"],
        )

    monkeypatch.setattr(git_worktree, "worktree_add", fake_worktree_add)
    monkeypatch.setattr(git_worktree, "resolve_local_file_paths", fake_resolve)
    monkeypatch.setattr(git_worktree, "copy_local_files", fake_copy)

    result = GitWorktreeWorkspaceBackend(cfg).prepare(
        feature="feat",
        repos=["api", "web", "docs", "api"],
        mode="reuse",
        overwrite_local=True,
    )

    assert [repo.repo for repo in result.repos] == ["api", "web", "docs"]
    assert [repo.status for repo in result.repos] == ["created", "created", "reused"]
    assert worktree_calls == [
        (tmp_path / "api", tmp_path / "_wt" / "feat" / "api", "feat/feat", True),
        (tmp_path / "web", tmp_path / "_wt" / "feat" / "web", "feat/feat", False),
    ]
    assert resolve_calls[0] == (
        tmp_path / "api",
        [".env", "api.env"],
        ["*.secret", "api.*"],
    )
    assert copy_calls[0][-2:] == (["resolved.env"], True)
    assert result.repos[0].local_files.to_dict() == {
        "copied": ["resolved.env"],
        "skipped_missing": ["missing.env"],
        "skipped_existing": [".env.local"],
        "skipped_invalid": ["../bad", "/abs"],
    }
    assert result.workspace_file.endswith("feat.code-workspace")
    assert (tmp_path / "_wt" / "feat" / "feat.code-workspace").is_file()


def test_git_worktree_backend_validate_checks_existing_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    (tmp_path / "api").mkdir()
    feature_dir = tmp_path / "_wt" / "feat"
    worktree = feature_dir / "api"
    worktree.mkdir(parents=True)
    workspace_file = feature_dir / "feat.code-workspace"
    workspace_file.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        git_worktree,
        "is_git_repo",
        lambda path: path in {tmp_path / "api", worktree},
    )

    result = GitWorktreeWorkspaceBackend(cfg).prepare(
        feature="feat",
        repos=["api"],
        mode="validate",
        copy_local=True,
    )

    assert result.mode == "validate"
    assert result.workspace_file == str(workspace_file)
    assert result.repos[0].status == "reused"
    assert result.repos[0].local_files == WorkspaceLocalFilesResult()


def test_git_worktree_backend_validate_allows_missing_workspace_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    (tmp_path / "_wt" / "feat").mkdir(parents=True)
    monkeypatch.setattr(git_worktree, "is_git_repo", lambda _path: True)

    result = GitWorktreeWorkspaceBackend(cfg).prepare(
        feature="feat",
        repos=[],
        mode="validate",
    )

    assert result.workspace_file == ""


def test_git_worktree_backend_error_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    backend = GitWorktreeWorkspaceBackend(cfg)

    with pytest.raises(ValueError, match="Feature workspace"):
        backend.prepare(feature="missing", repos=[], mode="validate")

    with pytest.raises(ValueError, match="not found under root"):
        backend.prepare(feature="feat", repos=["missing"], mode="reuse")

    (tmp_path / "plain").mkdir()
    monkeypatch.setattr(git_worktree, "is_git_repo", lambda _path: False)
    with pytest.raises(ValueError, match="not a git repo"):
        backend.prepare(feature="feat", repos=["plain"], mode="reuse")

    (tmp_path / "api").mkdir()
    feature_dir = tmp_path / "_wt" / "feat"
    (feature_dir / "api").mkdir(parents=True)
    monkeypatch.setattr(
        git_worktree,
        "is_git_repo",
        lambda path: path == tmp_path / "api",
    )
    with pytest.raises(ValueError, match="not a git repo"):
        backend.prepare(feature="feat", repos=["api"], mode="reuse")

    monkeypatch.setattr(
        git_worktree,
        "is_git_repo",
        lambda path: path in {tmp_path / "api", feature_dir / "api"},
    )
    with pytest.raises(ValueError, match="already exists"):
        backend.prepare(feature="feat", repos=["api"], mode="create")

    (feature_dir / "api").rmdir()
    with pytest.raises(ValueError, match="not found at"):
        backend.prepare(feature="feat", repos=["api"], mode="validate")


def test_git_worktree_backend_skips_local_copy_when_disabled_or_no_specs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    cfg.local_files.enabled = True
    cfg.local_files.overwrite = True
    (tmp_path / "api").mkdir()

    monkeypatch.setattr(git_worktree, "is_git_repo", lambda path: path.exists())
    monkeypatch.setattr(git_worktree, "branch_exists", lambda _repo, _branch: False)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda path: path.name == "api")
    monkeypatch.setattr(
        git_worktree,
        "worktree_add",
        lambda _repo, worktree_path, _branch, create: worktree_path.mkdir(parents=True),
    )
    resolve_calls = 0

    def fake_resolve(_repo_path: Path, *, paths: list[str], patterns: list[str]):
        nonlocal resolve_calls
        resolve_calls += 1
        return [], []

    monkeypatch.setattr(git_worktree, "resolve_local_file_paths", fake_resolve)

    explicit_disabled = GitWorktreeWorkspaceBackend(cfg).prepare(
        feature="feat-disabled",
        repos=["api"],
        mode="reuse",
        copy_local=False,
    )
    config_enabled_but_no_specs = GitWorktreeWorkspaceBackend(cfg).prepare(
        feature="feat-no-specs",
        repos=["api"],
        mode="reuse",
        copy_local=None,
        overwrite_local=None,
    )

    assert explicit_disabled.repos[0].local_files == WorkspaceLocalFilesResult()
    assert config_enabled_but_no_specs.repos[0].local_files == WorkspaceLocalFilesResult()
    assert resolve_calls == 1


@pytest.mark.parametrize("feature", ["../escape", "/tmp/escape", ".", "feat/nested"])
def test_git_worktree_backend_rejects_unsafe_feature_names(
    tmp_path: Path,
    feature: str,
) -> None:
    backend = GitWorktreeWorkspaceBackend(_cfg(tmp_path))

    with pytest.raises(ValueError, match="Feature name"):
        backend.prepare(feature=feature, repos=[], mode="state-only")


@pytest.mark.parametrize("repo", ["../outside", "/tmp/outside", ".", "apps/api"])
def test_git_worktree_backend_rejects_unsafe_repo_names(
    tmp_path: Path,
    repo: str,
) -> None:
    backend = GitWorktreeWorkspaceBackend(_cfg(tmp_path))

    with pytest.raises(ValueError, match="Repo name"):
        backend.prepare(feature="feat", repos=[repo], mode="reuse")


def test_git_worktree_backend_rejects_source_repo_symlink_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    backend = GitWorktreeWorkspaceBackend(_cfg(root))

    with pytest.raises(ValueError, match="Source repo must stay under"):
        backend.prepare(feature="feat", repos=["escaped"], mode="reuse")


def test_git_worktree_backend_preflights_repos_before_creating_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "api").mkdir()
    monkeypatch.setattr(git_worktree, "is_git_repo", lambda path: path == tmp_path / "api")
    worktree_calls: list[Path] = []

    def fake_worktree_add(
        _repo_path: Path,
        worktree_path: Path,
        _branch: str,
        _create: bool,
    ) -> None:
        worktree_calls.append(worktree_path)
        worktree_path.mkdir(parents=True)

    monkeypatch.setattr(git_worktree, "worktree_add", fake_worktree_add)

    with pytest.raises(ValueError, match="Repo 'missing' not found"):
        GitWorktreeWorkspaceBackend(_cfg(tmp_path)).prepare(
            feature="feat",
            repos=["api", "missing"],
            mode="reuse",
        )

    assert worktree_calls == []
    assert not (tmp_path / "_wt" / "feat" / "api").exists()
