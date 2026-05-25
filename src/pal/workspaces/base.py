from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


STATE_ONLY_WORKSPACE_MODE = "state-only"
VALID_WORKSPACE_MODES = frozenset({STATE_ONLY_WORKSPACE_MODE, "create", "reuse", "validate"})


def validate_workspace_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in VALID_WORKSPACE_MODES:
        valid = ", ".join(sorted(VALID_WORKSPACE_MODES))
        raise ValueError(f"Unknown workspace mode '{mode}'. Expected one of: {valid}.")
    return normalized


@dataclass(frozen=True)
class WorkspaceLocalFilesResult:
    copied: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "copied": list(self.copied),
            "skipped_missing": list(self.skipped_missing),
            "skipped_existing": list(self.skipped_existing),
            "skipped_invalid": list(self.skipped_invalid),
        }


@dataclass(frozen=True)
class WorkspaceRepoResult:
    repo: str
    source_path: str
    worktree_path: str
    branch: str
    status: str
    local_files: WorkspaceLocalFilesResult = field(default_factory=WorkspaceLocalFilesResult)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "source_path": self.source_path,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "status": self.status,
            "local_files": self.local_files.to_dict(),
        }


@dataclass(frozen=True)
class WorkspacePrepareResult:
    feature: str
    mode: str
    workspace_dir: str
    workspace_file: str
    repos: list[WorkspaceRepoResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "mode": self.mode,
            "workspace_dir": self.workspace_dir,
            "workspace_file": self.workspace_file,
            "repos": [repo.to_dict() for repo in self.repos],
        }


class WorkspaceBackend(Protocol):
    def prepare(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str,
        copy_local: bool | None = None,
        overwrite_local: bool | None = None,
    ) -> WorkspacePrepareResult: ...
