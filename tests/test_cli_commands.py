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


def test_run_codex_forwards_args_to_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def fake_run_codex(workspace_dir: Path, _cfg, extra_args=None):  # noqa: ANN001
        calls.append((workspace_dir, list(extra_args or [])))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_codex_interactive", fake_run_codex)

    result = runner.invoke(
        app,
        ["run", feature, "codex", "--root", str(root), "resume", "019b947b-ff0f-7ff3-8a49"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [(root / "_wt" / feature, ["resume", "019b947b-ff0f-7ff3-8a49"])]


def test_plan_claude_injects_permission_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        calls.append((workspace_dir, list(extra_args or [])))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["plan", feature, "claude", "--root", str(root), "Audit the repo"])
    assert result.exit_code == 0, result.output
    assert calls == [
        (root / "_wt" / feature, ["--permission-mode", "plan", "Audit the repo"]),
    ]


def test_implement_claude_respects_explicit_permission_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        calls.append((workspace_dir, list(extra_args or [])))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(
        app,
        [
            "implement",
            feature,
            "claude",
            "--root",
            str(root),
            "--permission-mode",
            "plan",
            "Dry run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [(root / "_wt" / feature, ["--permission-mode", "plan", "Dry run"])]


def test_run_claude_applies_agent_add_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        seen.append(list(add_dirs or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)
    (root / ".pal.toml").write_text('[agent]\nadd_dirs = ["~/.npm"]\n', encoding="utf-8")

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["run", feature, "claude", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert seen == [["~/.npm"]]


def test_run_claude_injects_default_permission_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        assert add_dirs == []
        calls.append(list(extra_args or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["run", feature, "claude", "--root", str(root), "Continue"])
    assert result.exit_code == 0, result.output
    assert calls == [["--permission-mode", "acceptEdits", "Continue"]]


def test_plan_claude_uses_internal_plan_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        assert add_dirs == []
        calls.append(list(extra_args or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)
    (root / ".pal.toml").write_text(
        '[claude]\npermission_mode = "acceptEdits"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["plan", feature, "claude", "--root", str(root), "Plan this"])
    assert result.exit_code == 0, result.output
    assert calls == [["--permission-mode", "plan", "Plan this"]]


def test_implement_claude_uses_configured_permission_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        assert add_dirs == []
        calls.append(list(extra_args or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)
    (root / ".pal.toml").write_text(
        '[claude]\npermission_mode = "plan"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["implement", feature, "claude", "--root", str(root), "Ship it"])
    assert result.exit_code == 0, result.output
    assert calls == [["--permission-mode", "plan", "Ship it"]]


def test_run_claude_merges_agent_and_claude_add_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        seen.append(list(add_dirs or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)
    (root / ".pal.toml").write_text(
        '[agent]\nadd_dirs = ["~/.npm"]\n\n[claude]\nadd_dirs = ["~/.cache/claude"]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(app, ["run", feature, "claude", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert seen == [["~/.npm", "~/.cache/claude"]]


def test_run_claude_blocks_bypass_permissions_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        raise AssertionError("runner should not be called when config blocks bypass permissions")

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(
        app,
        [
            "run",
            feature,
            "claude",
            "--root",
            str(root),
            "--permission-mode",
            "bypassPermissions",
        ],
    )
    assert result.exit_code != 0, result.output
    assert "allow_bypass_permissions=false" in result.output


def test_run_claude_allows_bypass_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run_claude(workspace_dir: Path, *, add_dirs=None, extra_args=None):  # noqa: ANN001
        assert workspace_dir == root / "_wt" / feature
        calls.append(list(extra_args or []))

    root = tmp_path / "projects"
    feature = "email"
    (root / "_wt" / feature).mkdir(parents=True)
    (root / ".pal.toml").write_text(
        "[claude]\nallow_bypass_permissions = true\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("pal.cli.run_claude_interactive", fake_run_claude)

    result = runner.invoke(
        app,
        [
            "run",
            feature,
            "claude",
            "--root",
            str(root),
            "--permission-mode",
            "bypassPermissions",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [["--permission-mode", "bypassPermissions"]]
