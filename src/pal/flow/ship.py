from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..git import is_git_repo
from ..identifiers import IdentifierError, normalize_repo_name, safe_child_path
from .providers.command import LocalCommandRunner


class FlowShipError(ValueError):
    pass


@dataclass(frozen=True)
class FlowShipRepo:
    repo: str
    path: str
    branch: str
    base: str
    head_sha: str
    changed: bool
    status_short: str
    porcelain: str
    diff_stat: str
    commit_status: str = "not_requested"
    push_status: str = "not_requested"
    pr_status: str = "not_requested"
    pr_url: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "path": self.path,
            "branch": self.branch,
            "base": self.base,
            "head_sha": self.head_sha,
            "changed": self.changed,
            "status_short": self.status_short,
            "porcelain": self.porcelain,
            "diff_stat": self.diff_stat,
            "commit_status": self.commit_status,
            "push_status": self.push_status,
            "pr_status": self.pr_status,
            "pr_url": self.pr_url,
            "error": self.error,
        }


@dataclass(frozen=True)
class FlowShipSummary:
    feature: str
    run_id: str
    base: str
    title: str
    dry_run: bool
    commit_requested: bool
    push_requested: bool
    pr_requested: bool
    draft: bool
    repos: list[FlowShipRepo]
    paths: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "failed" if any(repo.failed for repo in self.repos) else "completed"

    def with_paths(self, paths: dict[str, str]) -> FlowShipSummary:
        return replace(self, paths={**self.paths, **paths})

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "run_id": self.run_id,
            "status": self.status,
            "base": self.base,
            "title": self.title,
            "dry_run": self.dry_run,
            "commit_requested": self.commit_requested,
            "push_requested": self.push_requested,
            "pr_requested": self.pr_requested,
            "draft": self.draft,
            "paths": dict(self.paths),
            "repos": [repo.to_dict() for repo in self.repos],
        }


class FlowShipper:
    def __init__(self, runner: LocalCommandRunner | None = None) -> None:
        self.runner = runner or LocalCommandRunner()

    def ship(
        self,
        *,
        feature: str,
        run_id: str,
        feature_dir: Path,
        repos: list[str],
        base: str,
        title: str,
        body_file: Path,
        dry_run: bool,
        commit: bool,
        commit_message: str,
        push: bool,
        create_pr: bool,
        draft: bool,
    ) -> FlowShipSummary:
        feature_dir = feature_dir.resolve()
        repo_paths = self._repo_paths(feature_dir, repos)
        return FlowShipSummary(
            feature=feature,
            run_id=run_id,
            base=base,
            title=title,
            dry_run=dry_run,
            commit_requested=commit,
            push_requested=push,
            pr_requested=create_pr,
            draft=draft,
            repos=[
                self._ship_repo(
                    repo=repo,
                    repo_path=repo_path,
                    base=base,
                    title=title,
                    body_file=body_file,
                    dry_run=dry_run,
                    commit=commit,
                    commit_message=commit_message,
                    push=push,
                    create_pr=create_pr,
                    draft=draft,
                )
                for repo, repo_path in repo_paths
            ],
        )

    def _repo_paths(self, feature_dir: Path, repos: list[str]) -> list[tuple[str, Path]]:
        repo_names = list(dict.fromkeys(repo.strip() for repo in repos))
        if not repo_names:
            repo_names = sorted(
                child.name
                for child in feature_dir.iterdir()
                if child.is_dir() and is_git_repo(child)
            )
        if not repo_names:
            raise FlowShipError(f"No git repos found in feature workspace: {feature_dir}")
        repo_paths: list[tuple[str, Path]] = []
        for repo in repo_names:
            repo_path = _repo_path(feature_dir, repo)
            if not repo_path.exists():
                raise FlowShipError(f"Repo worktree '{repo}' not found at {repo_path}.")
            if not is_git_repo(repo_path):
                raise FlowShipError(f"Repo worktree '{repo}' is not a git repo: {repo_path}")
            repo_paths.append((repo, repo_path))
        return repo_paths

    def _ship_repo(
        self,
        *,
        repo: str,
        repo_path: Path,
        base: str,
        title: str,
        body_file: Path,
        dry_run: bool,
        commit: bool,
        commit_message: str,
        push: bool,
        create_pr: bool,
        draft: bool,
    ) -> FlowShipRepo:
        branch = self._git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        head_sha = self._git(repo_path, ["rev-parse", "HEAD"])
        status_short = self._git(repo_path, ["status", "-sb"])
        porcelain = self._git(repo_path, ["status", "--porcelain"])
        diff_stat = self._git(repo_path, ["diff", "--stat", "HEAD"], allow_failure=True)
        repo_result = FlowShipRepo(
            repo=repo,
            path=str(repo_path),
            branch=branch,
            base=base,
            head_sha=head_sha,
            changed=bool(porcelain.strip()),
            status_short=status_short,
            porcelain=porcelain,
            diff_stat=diff_stat,
        )
        repo_result = self._maybe_commit(repo_result, repo_path, dry_run, commit, commit_message)
        repo_result = self._maybe_push(repo_result, repo_path, dry_run, push)
        return self._maybe_create_pr(
            repo_result,
            repo_path,
            dry_run,
            create_pr,
            draft,
            title,
            body_file,
        )

    def _maybe_commit(
        self,
        repo: FlowShipRepo,
        repo_path: Path,
        dry_run: bool,
        commit: bool,
        commit_message: str,
    ) -> FlowShipRepo:
        if not commit:
            return repo
        if not repo.changed:
            return replace(repo, commit_status="clean")
        if dry_run:
            return replace(repo, commit_status="would_commit")
        add = self._run(["git", "add", "-A"], cwd=repo_path)
        if add.returncode != 0:
            return replace(
                repo,
                commit_status="failed",
                error=_command_error("git add", add.stderr),
            )
        committed = self._run(["git", "commit", "-m", commit_message], cwd=repo_path)
        if committed.returncode != 0:
            return replace(
                repo,
                commit_status="failed",
                error=_command_error("git commit", committed.stderr),
            )
        return replace(
            repo,
            changed=False,
            head_sha=self._git(repo_path, ["rev-parse", "HEAD"]),
            status_short=self._git(repo_path, ["status", "-sb"]),
            porcelain=self._git(repo_path, ["status", "--porcelain"]),
            diff_stat=self._git(repo_path, ["diff", "--stat", "HEAD"], allow_failure=True),
            commit_status="committed",
        )

    def _maybe_push(
        self,
        repo: FlowShipRepo,
        repo_path: Path,
        dry_run: bool,
        push: bool,
    ) -> FlowShipRepo:
        if not push or repo.failed:
            return repo
        if dry_run:
            return replace(repo, push_status="would_push")
        pushed = self._run(["git", "push", "-u", "origin", repo.branch], cwd=repo_path)
        if pushed.returncode != 0:
            return replace(
                repo,
                push_status="failed",
                error=_command_error("git push", pushed.stderr),
            )
        return replace(repo, push_status="pushed")

    def _maybe_create_pr(
        self,
        repo: FlowShipRepo,
        repo_path: Path,
        dry_run: bool,
        create_pr: bool,
        draft: bool,
        title: str,
        body_file: Path,
    ) -> FlowShipRepo:
        if not create_pr or repo.failed:
            return repo
        if dry_run:
            return replace(repo, pr_status="would_create")
        existing = self._run(
            ["gh", "pr", "view", "--head", repo.branch, "--json", "url", "--jq", ".url"],
            cwd=repo_path,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return replace(repo, pr_status="existing", pr_url=existing.stdout.strip())
        command = [
            "gh",
            "pr",
            "create",
            "--base",
            repo.base,
            "--head",
            repo.branch,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
        if draft:
            command.append("--draft")
        created = self._run(command, cwd=repo_path)
        if created.returncode != 0:
            return replace(
                repo,
                pr_status="failed",
                error=_command_error("gh pr create", created.stderr),
            )
        return replace(repo, pr_status="created", pr_url=created.stdout.strip())

    def _git(self, repo_path: Path, args: list[str], *, allow_failure: bool = False) -> str:
        result = self._run(["git", *args], cwd=repo_path)
        if result.returncode == 0:
            return result.stdout.strip()
        if allow_failure:
            return ""
        raise FlowShipError(_command_error("git " + " ".join(args), result.stderr))

    def _run(self, command: list[str], *, cwd: Path):
        return self.runner.run(command, cwd=cwd)


def _command_error(command: str, stderr: str) -> str:
    detail = stderr.strip() or "no stderr"
    return f"{command} failed: {detail}"


def _repo_path(feature_dir: Path, repo: str) -> Path:
    try:
        name = normalize_repo_name(repo)
        return safe_child_path(feature_dir, name, "Repo worktree")
    except IdentifierError as exc:
        raise FlowShipError(f"Repo worktree must stay inside feature workspace: {repo}") from exc
