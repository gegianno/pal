from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from pal.flow.providers.base import CommandResult
from pal.flow.pr import FlowPrError, FlowPrManager, FlowPrSummary


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "pal@example.com")
    _git(path, "config", "user.name", "pal")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")
    return path


class FakePrRunner:
    def __init__(
        self,
        *,
        changed: bool = True,
        branch_rc: int = 0,
        diff_rc: int = 0,
        add_rc: int = 0,
        commit_rc: int = 0,
        push_rc: int = 0,
        existing_pr: str = "",
        create_rc: int = 0,
    ) -> None:
        self.changed = changed
        self.branch_rc = branch_rc
        self.diff_rc = diff_rc
        self.add_rc = add_rc
        self.commit_rc = commit_rc
        self.push_rc = push_rc
        self.existing_pr = existing_pr
        self.create_rc = create_rc
        self.committed = False
        self.calls: list[tuple[list[str], Path | None]] = []

    def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201, ARG002
        self.calls.append((command, cwd))
        if command[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return CommandResult(
                returncode=self.branch_rc, stdout="feat/test\n", stderr="bad ref\n"
            )
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return CommandResult(returncode=0, stdout=("newsha" if self.committed else "oldsha"))
        if command[:3] == ["git", "status", "-sb"]:
            suffix = "" if self.committed or not self.changed else "\n M README.md"
            return CommandResult(returncode=0, stdout=f"## feat/test{suffix}\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(
                returncode=0,
                stdout="" if self.committed or not self.changed else " M README.md\n",
            )
        if command[:3] == ["git", "diff", "--stat"]:
            return CommandResult(returncode=self.diff_rc, stdout=" README.md | 1 +\n")
        if command[:3] == ["git", "add", "-A"]:
            return CommandResult(returncode=self.add_rc, stderr="add failed\n")
        if command[:2] == ["git", "commit"]:
            if self.commit_rc == 0:
                self.committed = True
            return CommandResult(returncode=self.commit_rc, stderr="commit failed\n")
        if command[:2] == ["git", "push"]:
            return CommandResult(returncode=self.push_rc, stderr="push failed\n")
        if command[:3] == ["gh", "pr", "view"]:
            return CommandResult(
                returncode=0 if self.existing_pr else 1,
                stdout=f"{self.existing_pr}\n" if self.existing_pr else "",
            )
        if command[:3] == ["gh", "pr", "create"]:
            return CommandResult(
                returncode=self.create_rc,
                stdout="https://example.test/pr/1\n",
                stderr="pr failed\n",
            )
        raise AssertionError(f"unexpected command: {command}")


def _pr(
    feature_dir: Path,
    *,
    runner: FakePrRunner | None = None,
    repos: list[str] | None = None,
    dry_run: bool = True,
    commit: bool = False,
    push: bool = False,
    create_pr: bool = False,
    draft: bool = False,
) -> FlowPrSummary:
    body_file = feature_dir / "body.md"
    body_file.write_text("body\n", encoding="utf-8")
    return FlowPrManager(runner).prepare(
        feature="feat",
        run_id="run_1",
        feature_dir=feature_dir,
        repos=list(repos or []),
        base="main",
        title="Open PR",
        body_file=body_file,
        dry_run=dry_run,
        commit=commit,
        commit_message="Open PR",
        push=push,
        create_pr=create_pr,
        draft=draft,
    )


def test_flow_pr_manager_discovers_repos_and_dry_runs_requested_actions(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    repo = _init_repo(feature_dir / "api")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")

    summary = _pr(feature_dir, commit=True, push=True, create_pr=True)

    assert summary.status == "completed"
    assert summary.to_dict()["status"] == "completed"
    assert summary.repos[0].repo == "api"
    assert summary.repos[0].changed is True
    assert summary.repos[0].commit_status == "would_commit"
    assert summary.repos[0].push_status == "would_push"
    assert summary.repos[0].pr_status == "would_create"


def test_flow_pr_manager_marks_clean_repo_without_committing(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    _init_repo(feature_dir / "api")

    summary = _pr(feature_dir, repos=["api"], dry_run=False, commit=True)

    assert summary.repos[0].changed is False
    assert summary.repos[0].commit_status == "clean"


def test_flow_pr_manager_rejects_missing_or_invalid_repos(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    feature_dir.mkdir(parents=True)

    with pytest.raises(FlowPrError, match="No git repos"):
        _pr(feature_dir)
    with pytest.raises(FlowPrError, match="not found"):
        _pr(feature_dir, repos=["api"])

    (feature_dir / "api").mkdir()
    with pytest.raises(FlowPrError, match="not a git repo"):
        _pr(feature_dir, repos=["api"])


@pytest.mark.parametrize("repo", ["../outside", "/tmp/outside", ".", " "])
def test_flow_pr_manager_rejects_repo_paths_outside_feature_workspace(
    tmp_path: Path,
    repo: str,
) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    outside = _init_repo(tmp_path / "_wt" / "outside")
    feature_dir.mkdir(parents=True)

    assert outside.is_dir()
    with pytest.raises(FlowPrError, match="inside feature workspace|must not be empty"):
        _pr(feature_dir, repos=[repo])


def test_flow_pr_manager_rejects_symlinked_repo_escape(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    outside = _init_repo(tmp_path / "_wt" / "outside")
    feature_dir.mkdir(parents=True)
    (feature_dir / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FlowPrError, match="inside feature workspace"):
        _pr(feature_dir, repos=["linked"])


def test_flow_pr_manager_commits_pushes_and_reuses_existing_pr(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    _init_repo(feature_dir / "api")
    runner = FakePrRunner(existing_pr="https://example.test/pr/old")

    summary = _pr(
        feature_dir,
        runner=runner,
        repos=["api", "api"],
        dry_run=False,
        commit=True,
        push=True,
        create_pr=True,
    )

    assert [repo.repo for repo in summary.repos] == ["api"]
    assert summary.repos[0].commit_status == "committed"
    assert summary.repos[0].push_status == "pushed"
    assert summary.repos[0].pr_status == "existing"
    assert summary.repos[0].pr_url == "https://example.test/pr/old"
    view_call = [call[0] for call in runner.calls if call[0][:3] == ["gh", "pr", "view"]][0]
    assert view_call == ["gh", "pr", "view", "feat/test", "--json", "url", "--jq", ".url"]
    assert not any(call[0][:3] == ["gh", "pr", "create"] for call in runner.calls)


def test_flow_pr_manager_creates_draft_pr(tmp_path: Path) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    _init_repo(feature_dir / "api")
    runner = FakePrRunner(changed=False)

    summary = _pr(
        feature_dir,
        runner=runner,
        repos=["api"],
        dry_run=False,
        create_pr=True,
        draft=True,
    )

    assert summary.repos[0].pr_status == "created"
    assert summary.repos[0].pr_url == "https://example.test/pr/1"
    create_call = [call[0] for call in runner.calls if call[0][:3] == ["gh", "pr", "create"]][0]
    assert "--draft" in create_call


@pytest.mark.parametrize(
    ("runner", "kwargs", "status", "error"),
    [
        (FakePrRunner(add_rc=1), {"commit": True}, "commit_status", "git add failed"),
        (FakePrRunner(commit_rc=1), {"commit": True}, "commit_status", "git commit failed"),
        (FakePrRunner(push_rc=1), {"push": True}, "push_status", "git push failed"),
        (FakePrRunner(create_rc=1), {"create_pr": True}, "pr_status", "gh pr create failed"),
    ],
)
def test_flow_pr_manager_records_action_failures(
    tmp_path: Path,
    runner: FakePrRunner,
    kwargs: dict[str, bool],
    status: str,
    error: str,
) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    _init_repo(feature_dir / "api")

    summary = _pr(feature_dir, runner=runner, repos=["api"], dry_run=False, **kwargs)

    assert summary.status == "failed"
    assert getattr(summary.repos[0], status) == "failed"
    assert error in summary.repos[0].error
    assert summary.repos[0].failed is True


def test_flow_pr_manager_handles_diff_stat_failure_and_raises_for_required_git_failure(
    tmp_path: Path,
) -> None:
    feature_dir = tmp_path / "_wt" / "feat"
    _init_repo(feature_dir / "api")

    diff_summary = _pr(
        feature_dir,
        runner=FakePrRunner(diff_rc=1),
        repos=["api"],
        dry_run=False,
    )

    assert diff_summary.repos[0].diff_stat == ""
    with pytest.raises(FlowPrError, match="git rev-parse"):
        _pr(feature_dir, runner=FakePrRunner(branch_rc=1), repos=["api"])


def test_flow_pr_summary_merges_paths() -> None:
    summary = FlowPrSummary(
        feature="feat",
        run_id="run_1",
        base="main",
        title="Open PR",
        dry_run=True,
        commit_requested=False,
        push_requested=False,
        pr_requested=False,
        draft=False,
        repos=[],
        paths={"body": "body.md"},
    )

    assert summary.with_paths({"manifest": "manifest.json"}).paths == {
        "body": "body.md",
        "manifest": "manifest.json",
    }
