from __future__ import annotations

from pathlib import Path

from ..git import branch_exists, is_git_repo, worktree_add
from ..local_files import copy_local_files, resolve_local_file_paths
from ..vscode import write_code_workspace
from .base import (
    STATE_ONLY_WORKSPACE_MODE,
    WorkspaceLocalFilesResult,
    WorkspacePrepareResult,
    WorkspaceRepoResult,
    validate_workspace_mode,
)


class GitWorktreeWorkspaceBackend:
    def __init__(self, cfg) -> None:  # noqa: ANN001
        self.cfg = cfg

    def prepare(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str,
        copy_local: bool | None = None,
        overwrite_local: bool | None = None,
    ) -> WorkspacePrepareResult:
        normalized_mode = validate_workspace_mode(mode)
        feature_dir = self.feature_dir(feature)
        unique_repos = list(dict.fromkeys(repos))

        if normalized_mode == STATE_ONLY_WORKSPACE_MODE:
            return WorkspacePrepareResult(
                feature=feature,
                mode=normalized_mode,
                workspace_dir=str(feature_dir),
                workspace_file="",
                repos=[],
            )

        if normalized_mode == "validate" and not feature_dir.exists():
            raise ValueError(f"Feature workspace '{feature}' not found at {feature_dir}.")
        if normalized_mode != "validate":
            feature_dir.mkdir(parents=True, exist_ok=True)

        repo_results = [
            self._prepare_repo(
                feature=feature,
                repo=repo,
                mode=normalized_mode,
                copy_local=copy_local,
                overwrite_local=overwrite_local,
            )
            for repo in unique_repos
        ]
        workspace_file = self._workspace_file(feature, feature_dir, normalized_mode)

        return WorkspacePrepareResult(
            feature=feature,
            mode=normalized_mode,
            workspace_dir=str(feature_dir),
            workspace_file=workspace_file,
            repos=repo_results,
        )

    def feature_dir(self, feature: str) -> Path:
        return self.cfg.worktree_root / feature

    def worktree_path(self, feature: str, repo: str) -> Path:
        return self.feature_dir(feature) / repo

    def branch(self, feature: str) -> str:
        return f"{self.cfg.branch_prefix}/{feature}"

    def _prepare_repo(
        self,
        *,
        feature: str,
        repo: str,
        mode: str,
        copy_local: bool | None,
        overwrite_local: bool | None,
    ) -> WorkspaceRepoResult:
        repo_path = self._require_source_repo(repo)
        worktree_path = self.worktree_path(feature, repo)
        branch = self.branch(feature)

        if worktree_path.exists():
            if not is_git_repo(worktree_path):
                raise ValueError(f"Workspace path exists but is not a git repo: {worktree_path}")
            if mode == "create":
                raise ValueError(f"Worktree already exists for repo '{repo}': {worktree_path}")
            status = "reused"
        else:
            if mode == "validate":
                raise ValueError(f"Worktree for repo '{repo}' not found at {worktree_path}.")
            worktree_add(
                repo_path,
                worktree_path,
                branch,
                create=not branch_exists(repo_path, branch),
            )
            status = "created"

        local_files = WorkspaceLocalFilesResult()
        if mode != "validate" and self._copy_local_enabled(copy_local):
            local_files = self._sync_local_files(
                repo_path,
                worktree_path,
                repo=repo,
                overwrite=self._overwrite_local_enabled(overwrite_local),
            )

        return WorkspaceRepoResult(
            repo=repo,
            source_path=str(repo_path),
            worktree_path=str(worktree_path),
            branch=branch,
            status=status,
            local_files=local_files,
        )

    def _require_source_repo(self, repo: str) -> Path:
        repo_path = self.cfg.root / repo
        if not repo_path.exists():
            raise ValueError(f"Repo '{repo}' not found under root '{self.cfg.root}'.")
        if not is_git_repo(repo_path):
            raise ValueError(f"'{repo_path}' is not a git repo.")
        return repo_path

    def _workspace_file(self, feature: str, feature_dir: Path, mode: str) -> str:
        if mode == "validate":
            candidate = feature_dir / f"{feature}.code-workspace"
            return str(candidate) if candidate.exists() else ""
        return str(write_code_workspace(feature_dir, feature))

    def _copy_local_enabled(self, copy_local: bool | None) -> bool:
        return self.cfg.local_files.enabled if copy_local is None else copy_local

    def _overwrite_local_enabled(self, overwrite_local: bool | None) -> bool:
        return self.cfg.local_files.overwrite if overwrite_local is None else overwrite_local

    def _sync_local_files(
        self,
        repo_path: Path,
        worktree_path: Path,
        *,
        repo: str,
        overwrite: bool,
    ) -> WorkspaceLocalFilesResult:
        repo_cfg = self.cfg.local_files.repos.get(repo)
        resolved_paths, skipped_invalid = resolve_local_file_paths(
            repo_path,
            paths=list(self.cfg.local_files.paths) + (list(repo_cfg.paths) if repo_cfg else []),
            patterns=list(self.cfg.local_files.patterns)
            + (list(repo_cfg.patterns) if repo_cfg else []),
        )
        if not resolved_paths and not skipped_invalid:
            return WorkspaceLocalFilesResult()

        result = copy_local_files(
            repo_path,
            worktree_path,
            paths=resolved_paths,
            overwrite=overwrite,
        )
        return WorkspaceLocalFilesResult(
            copied=[path.as_posix() for path in result.copied],
            skipped_missing=[path.as_posix() for path in result.skipped_missing],
            skipped_existing=[path.as_posix() for path in result.skipped_existing],
            skipped_invalid=[*skipped_invalid, *result.skipped_invalid],
        )
