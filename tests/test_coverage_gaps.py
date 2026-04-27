from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
import typer
from typer.testing import CliRunner

import pal.claude as claude_module
import pal.cli as cli_module
import pal.codex as codex_module
import pal.git as git_module
from pal.cli import app
from pal.completion import complete_agent, complete_feature, complete_repo, complete_repo_in_feature
from pal.config import (
    AgentConfig,
    ClaudeConfig,
    CodexConfig,
    PalConfig,
    _apply_dict,
    load_config,
)
from pal.local_files import copy_local_files, resolve_local_file_paths
from pal.vscode import write_code_workspace


runner = CliRunner()


def _cfg(root: Path) -> PalConfig:
    return PalConfig(root=root, worktree_root=root / "_wt")


def test_cli_helper_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_version(_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(importlib.metadata, "version", raise_version)
    assert cli_module._pal_version() == "0.0.0"
    assert cli_module._print_version(False) is None

    monkeypatch.setattr(cli_module, "exists_on_path", lambda exe: exe == "cursor")
    assert cli_module._detect_editor("") == "cursor"
    assert cli_module._detect_editor("vim") == "vim"

    monkeypatch.setattr(cli_module, "exists_on_path", lambda exe: exe == "code")
    assert cli_module._detect_editor("") == "code"

    cfg = cli_module._cfg_from_ctx(tmp_path, tmp_path / "custom_wt", "bug")
    assert cfg.worktree_root == (tmp_path / "custom_wt").resolve()
    assert cfg.branch_prefix == "bug"

    assert cli_module._branch(_cfg(tmp_path), "abc") == "feat/abc"
    assert (
        cli_module._worktree_path(_cfg(tmp_path), "abc", "repo")
        == tmp_path / "_wt" / "abc" / "repo"
    )

    with pytest.raises(typer.BadParameter, match="not found"):
        cli_module._require_root_repo(_cfg(tmp_path), "missing")

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cli_module, "is_git_repo", lambda _p: False)
    with pytest.raises(typer.BadParameter, match="not a git repo"):
        cli_module._require_root_repo(_cfg(tmp_path), "repo")

    with pytest.raises(typer.BadParameter, match="Unknown agent"):
        cli_module._resolve_agent("bad")
    assert cli_module._resolve_agent(" CODEX ") == "codex"

    args = ["--flag", "value", "--other=x", "--lonely"]
    assert cli_module._has_flag(args, "--flag") is True
    assert cli_module._has_flag(args, "--other") is True
    assert cli_module._has_flag(args, "--missing") is False
    assert cli_module._flag_value(args, "--flag") == "value"
    assert cli_module._flag_value(args, "--other") == "x"
    assert cli_module._flag_value(args, "--lonely") is None
    assert cli_module._flag_value(args, "--missing") is None
    assert cli_module._remove_flag(args, "--flag") == ["--other=x", "--lonely"]
    assert cli_module._remove_flag(args, "--other") == ["--flag", "value", "--lonely"]


def test_agent_argument_helpers(tmp_path: Path) -> None:
    cfg = SimpleNamespace(
        agent=AgentConfig(add_dirs=["/tmp/a"]),
        codex=CodexConfig(add_dirs=["/tmp/a", "/tmp/b"]),
        claude=ClaudeConfig(
            permission_mode="",
            model="sonnet",
            add_dirs=["/tmp/a", "/tmp/c"],
            extra_args=["--verbose"],
            allow_bypass_permissions=False,
        ),
    )

    assert cli_module._effective_codex_config(cfg).add_dirs == ["/tmp/a", "/tmp/b"]
    assert cli_module._effective_claude_add_dirs(cfg) == ["/tmp/a", "/tmp/c"]
    assert cli_module._default_claude_mode_for_intent(cfg, "run") == ""
    assert cli_module._is_bypass_permission_mode("bypass-permissions") is True

    with pytest.raises(typer.BadParameter, match="Bypass permissions are disabled"):
        cli_module._validate_claude_permissions(cfg, ["--dangerously-skip-permissions"])

    effective = cli_module._effective_claude_args(cfg, "run", ["Prompt"])
    assert effective == ["--verbose", "--model", "sonnet", "Prompt"]

    cfg.claude.allow_bypass_permissions = True
    assert cli_module._effective_claude_args(
        cfg, "run", ["--permission-mode=bypass_permissions"]
    ) == ["--verbose", "--model", "sonnet", "--permission-mode=bypass_permissions"]

    assert cli_module._codex_plan_args([]) == ["/plan"]
    with pytest.raises(typer.BadParameter, match="accepts only a planning prompt"):
        cli_module._codex_plan_args(["--json"])
    with pytest.raises(typer.BadParameter, match="interactive planning only"):
        cli_module._codex_plan_args(["resume"])

    assert cli_module._flag_value(["--model=opus"], "--model") == "opus"
    assert tmp_path.exists()


def test_run_agent_codex_and_terminal_title_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[str] | None]] = []

    def fake_codex(workspace_dir: Path, _cfg: CodexConfig, extra_args=None) -> None:  # noqa: ANN001
        calls.append((workspace_dir, list(extra_args) if extra_args else None))

    monkeypatch.setattr(cli_module, "run_codex_interactive", fake_codex)
    cfg = SimpleNamespace(agent=AgentConfig(), codex=CodexConfig())
    cli_module._run_agent(tmp_path / "feat", cfg, "codex", "plan", [])
    assert calls == [(tmp_path / "feat", ["/plan"])]

    class NonTty:
        def isatty(self) -> bool:
            return False

        def write(self, _value: str) -> int:
            raise AssertionError("should not write for non-tty")

        def flush(self) -> None:
            raise AssertionError("should not flush for non-tty")

    monkeypatch.setattr(cli_module.sys, "stdout", NonTty())
    cli_module._set_terminal_title(feature="f", agent="codex")

    class DumbTty:
        def isatty(self) -> bool:
            return True

        def write(self, _value: str) -> int:
            raise AssertionError("should not write for dumb terminal")

        def flush(self) -> None:
            raise AssertionError("should not flush for dumb terminal")

    monkeypatch.setattr(cli_module.sys, "stdout", DumbTty())
    monkeypatch.setenv("TERM", "dumb")
    cli_module._set_terminal_title(feature="f", agent="codex")


def test_doctor_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "exists_on_path", lambda exe: exe in {"git", "codex", "cursor"})
    ok = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert "Codex CLI" in ok.output

    monkeypatch.setattr(cli_module, "exists_on_path", lambda _exe: False)
    bad = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert bad.exit_code == 1
    assert "At least one agent CLI is required" in bad.output


def test_cli_commands_uncovered_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    no_repos = runner.invoke(app, ["repos", "--root", str(root)])
    assert no_repos.exit_code == 1
    assert "No repos found" in no_repos.output

    no_features = runner.invoke(app, ["ls", "--root", str(root)])
    assert no_features.exit_code == 0
    assert "No feature workspaces yet" in no_features.output

    result = runner.invoke(app, ["status", "missing", "--root", str(root)])
    assert result.exit_code != 0
    assert "not found" in result.output

    result = runner.invoke(app, ["open", "missing", "--root", str(root)])
    assert result.exit_code != 0
    assert "not found" in result.output

    (root / "_wt" / "feat").mkdir(parents=True)
    popen_calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd: list[str]) -> None:
            popen_calls.append(cmd)

    monkeypatch.setattr(cli_module.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(cli_module, "_detect_editor", lambda _preferred: "code")
    result = runner.invoke(app, ["open", "feat", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert popen_calls and popen_calls[0][0] == "code"

    result = runner.invoke(app, ["run", "missing", "codex", "--root", str(root)])
    assert result.exit_code != 0
    assert "not found" in result.output

    result = runner.invoke(app, ["run", "feat", "bad", "--root", str(root)])
    assert result.exit_code != 0
    assert "Unknown agent" in result.output


def test_ensure_worktree_and_sync_local_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    repo = root / "repo"
    repo.mkdir(parents=True)
    cfg = _cfg(root)
    cfg.local_files.paths = [".env"]

    monkeypatch.setattr(cli_module, "is_git_repo", lambda _p: True)
    monkeypatch.setattr(cli_module, "branch_exists", lambda _repo_path, _branch: True)
    worktree_calls: list[tuple[Path, Path, str, bool]] = []

    def fake_worktree_add(repo_path: Path, wt_path: Path, branch: str, create: bool) -> None:
        worktree_calls.append((repo_path, wt_path, branch, create))
        wt_path.mkdir(parents=True)

    monkeypatch.setattr(cli_module, "worktree_add", fake_worktree_add)
    cli_module._ensure_worktree(cfg, "feat", "repo")
    cli_module._ensure_worktree(cfg, "feat", "repo")
    assert worktree_calls == [(repo, root / "_wt" / "feat" / "repo", "feat/feat", False)]

    monkeypatch.setattr(cli_module, "resolve_local_file_paths", lambda *_a, **_k: ([], []))
    assert cli_module._sync_local_files(cfg, "feat", "repo", overwrite=False) is None

    copy_calls: list[tuple[list[str], bool]] = []

    def fake_resolve(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return ([".env"], ["../bad"])

    def fake_copy(_src, _dst, *, paths, overwrite):  # noqa: ANN001
        copy_calls.append((paths, overwrite))
        return SimpleNamespace(
            copied=[Path(".env")],
            skipped_existing=[Path(".env.local")],
            skipped_invalid=["/abs"],
        )

    monkeypatch.setattr(cli_module, "resolve_local_file_paths", fake_resolve)
    monkeypatch.setattr(cli_module, "copy_local_files", fake_copy)
    cli_module._sync_local_files(cfg, "feat", "repo", overwrite=True)
    assert copy_calls == [([".env"], True)]

    def fake_copy_empty(_src, _dst, *, paths, overwrite):  # noqa: ANN001
        return SimpleNamespace(copied=[], skipped_existing=[], skipped_invalid=[])

    monkeypatch.setattr(cli_module, "resolve_local_file_paths", lambda *_a, **_k: ([".env"], []))
    monkeypatch.setattr(cli_module, "copy_local_files", fake_copy_empty)
    cli_module._sync_local_files(cfg, "feat", "repo", overwrite=False)


def test_new_and_add_copy_local_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "projects"
    (root / "repo").mkdir(parents=True)
    sync_calls: list[tuple[str, str, bool]] = []

    def fake_ensure(cfg, feature: str, repo: str) -> None:  # noqa: ANN001
        (cfg.worktree_root / feature / repo).mkdir(parents=True, exist_ok=True)

    def fake_sync(_cfg, feature: str, repo: str, *, overwrite: bool) -> None:  # noqa: ANN001
        sync_calls.append((feature, repo, overwrite))

    monkeypatch.setattr(cli_module, "_ensure_worktree", fake_ensure)
    monkeypatch.setattr(cli_module, "_sync_local_files", fake_sync)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda _p: True)

    result = runner.invoke(
        app,
        ["new", "feat", "repo", "--root", str(root), "--copy-local", "--overwrite-local"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        ["add", "feat", "repo", "--root", str(root), "--copy-local", "--overwrite-local"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["add", "feat", "repo", "--root", str(root), "--no-copy-local"])
    assert result.exit_code == 0, result.output
    assert sync_calls == [("feat", "repo", True), ("feat", "repo", True)]


def test_status_skips_non_repos_and_reports_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    feature_dir = root / "_wt" / "feat"
    (feature_dir / "repo").mkdir(parents=True)
    (feature_dir / "skip").mkdir()
    monkeypatch.setattr(cli_module, "is_git_repo", lambda p: p.name == "repo")
    monkeypatch.setattr(cli_module, "git_status_short", lambda _p: "## feat/branch\n")
    monkeypatch.setattr(cli_module, "git_porcelain", lambda _p: " M file.py")

    result = runner.invoke(app, ["status", "feat", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert "repo" in result.output
    assert "yes" in result.output
    assert "skip" not in result.output


def test_rename_validation_and_no_repo_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    old_dir = root / "_wt" / "old"
    old_dir.mkdir(parents=True)
    (root / "_wt" / "exists").mkdir(parents=True)

    result = runner.invoke(app, ["rename", "old", "old", "--root", str(root)])
    assert result.exit_code != 0
    assert "must be different" in result.output

    result = runner.invoke(app, ["rename", "missing", "new", "--root", str(root)])
    assert result.exit_code != 0
    assert "not found" in result.output

    result = runner.invoke(app, ["rename", "old", "exists", "--root", str(root)])
    assert result.exit_code != 0
    assert "already exists" in result.output

    result = runner.invoke(app, ["rename", "old", "new", "--root", str(root)], input="n\n")
    assert result.exit_code == 1

    result = runner.invoke(app, ["rename", "old", "new", "--root", str(root)], input="y\n")
    assert result.exit_code == 0, result.output
    assert (root / "_wt" / "new").exists()

    # Cover the repos confirmation panel with a declined confirmation.
    repo_dir = root / "_wt" / "withrepo" / "repo"
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr(cli_module, "is_git_repo", lambda p: p.name == "repo")
    result = runner.invoke(
        app, ["rename", "withrepo", "withrepo-new", "--root", str(root)], input="n\n"
    )
    assert result.exit_code == 1


def test_rename_repo_leftovers_and_rmdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    repo_root = root / "repo"
    old_dir = root / "_wt" / "old"
    repo_wt = old_dir / "repo"
    leftover = old_dir / "notes.md"
    repo_root.mkdir(parents=True)
    repo_wt.mkdir(parents=True)
    leftover.write_text("notes\n", encoding="utf-8")

    monkeypatch.setattr(cli_module, "is_git_repo", lambda p: p.name == "repo")
    moves: list[tuple[Path, Path, Path]] = []

    def fake_move(repo_path: Path, old_wt: Path, new_wt: Path) -> None:
        moves.append((repo_path, old_wt, new_wt))
        new_wt.parent.mkdir(parents=True, exist_ok=True)
        old_wt.rename(new_wt)

    monkeypatch.setattr(cli_module, "worktree_move", fake_move)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda p: p.name == "repo")
    result = runner.invoke(app, ["rename", "old", "new", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    assert moves == [(repo_root, repo_wt, root / "_wt" / "new" / "repo")]
    assert (root / "_wt" / "new" / "notes.md").exists()

    conflict_old = root / "_wt" / "conflict-old"
    (conflict_old / "repo").mkdir(parents=True)
    (conflict_old / "repo" / "nested").mkdir()
    (conflict_old / "repo" / "left.txt").write_text("x\n", encoding="utf-8")

    def fake_move_leaves_conflict(_repo_path: Path, old_wt: Path, new_wt: Path) -> None:
        new_wt.parent.mkdir(parents=True, exist_ok=True)
        new_wt.mkdir()
        (new_wt / "left.txt").write_text("existing\n", encoding="utf-8")

    monkeypatch.setattr(cli_module, "worktree_move", fake_move_leaves_conflict)
    result = runner.invoke(
        app,
        ["rename", "conflict-old", "conflict-new", "--root", str(root), "--yes"],
    )
    assert result.exit_code != 0
    assert "Cannot move leftover path" in result.output

    rmdir_old = root / "_wt" / "rmdir-old"
    (rmdir_old / "repo").mkdir(parents=True)

    def fake_move_for_rmdir(_repo_path: Path, old_wt: Path, new_wt: Path) -> None:
        new_wt.parent.mkdir(parents=True, exist_ok=True)
        old_wt.rename(new_wt)

    original_rmdir = Path.rmdir

    def fake_rmdir(path: Path) -> None:
        if path == rmdir_old:
            raise OSError("busy")
        original_rmdir(path)

    monkeypatch.setattr(cli_module, "worktree_move", fake_move_for_rmdir)
    monkeypatch.setattr(Path, "rmdir", fake_rmdir)
    result = runner.invoke(app, ["rename", "rmdir-old", "rmdir-new", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output


def test_rm_confirmation_and_error_detail_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    empty = root / "_wt" / "empty"
    empty.mkdir(parents=True)
    result = runner.invoke(app, ["rm", "missing", "--root", str(root)])
    assert result.exit_code != 0
    assert "not found" in result.output

    result = runner.invoke(app, ["rm", "empty", "--root", str(root)], input="n\n")
    assert result.exit_code == 1
    assert empty.exists()

    result = runner.invoke(app, ["rm", "empty", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    assert not empty.exists()

    empty_confirm = root / "_wt" / "empty-confirm"
    empty_confirm.mkdir(parents=True)
    result = runner.invoke(app, ["rm", "empty-confirm", "--root", str(root)], input="y\n")
    assert result.exit_code == 0, result.output

    repo_root = root / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    feature_dir = root / "_wt" / "feat"
    wt = feature_dir / "repo"
    wt.mkdir(parents=True)
    monkeypatch.setattr(cli_module, "is_git_repo", lambda p: p.name == "repo")

    calls: list[Path] = []

    def fake_remove(_repo_path: Path, worktree_path: Path, force: bool = True) -> None:
        assert force is True
        calls.append(worktree_path)
        worktree_path.rmdir()

    monkeypatch.setattr(cli_module, "worktree_remove", fake_remove)
    result = runner.invoke(app, ["rm", "feat", "--repo", "repo", "--root", str(root)], input="y\n")
    assert result.exit_code == 0, result.output
    assert calls == [wt]

    missing_wt_feature = root / "_wt" / "missing-wt"
    missing_wt_feature.mkdir(parents=True)
    result = runner.invoke(
        app,
        ["rm", "missing-wt", "--repo", "repo", "--root", str(root), "--yes"],
    )
    assert result.exit_code == 0, result.output

    decline_feature = root / "_wt" / "decline"
    (decline_feature / "repo").mkdir(parents=True)
    result = runner.invoke(app, ["rm", "decline", "--root", str(root)], input="n\n")
    assert result.exit_code == 1

    stdout_exc = subprocess.CalledProcessError(1, ["git"])
    stdout_exc.stdout = "stdout detail"
    output_exc = subprocess.CalledProcessError(2, ["git"])
    output_exc.output = b"output detail"
    details = [stdout_exc, output_exc, subprocess.CalledProcessError(3, ["git"])]
    for index, exc in enumerate(details):
        feature = f"bad{index}"
        bad_wt = root / "_wt" / feature / "repo"
        bad_wt.mkdir(parents=True, exist_ok=True)

        def fail_remove(
            _repo_path: Path, _worktree_path: Path, force: bool = True, *, exc=exc
        ) -> None:
            raise exc

        monkeypatch.setattr(cli_module, "worktree_remove", fail_remove)
        result = runner.invoke(app, ["rm", feature, "--root", str(root), "--yes"])
        assert result.exit_code == 1
        assert "Worktree Removal Issues" in result.output


def test_rm_refreshes_workspace_when_repos_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    (root / "repo1").mkdir(parents=True)
    (root / "repo2").mkdir()
    feature_dir = root / "_wt" / "feat"
    (feature_dir / "repo1").mkdir(parents=True)
    (feature_dir / "repo2").mkdir()
    monkeypatch.setattr(cli_module, "is_git_repo", lambda p: p.name in {"repo1", "repo2"})

    def fake_remove(_repo_path: Path, worktree_path: Path, force: bool = True) -> None:
        worktree_path.rmdir()

    monkeypatch.setattr(cli_module, "worktree_remove", fake_remove)
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda p: p.name == "repo2")
    result = runner.invoke(app, ["rm", "feat", "--repo", "repo1", "--root", str(root), "--yes"])
    assert result.exit_code == 0, result.output
    assert (feature_dir / "feat.code-workspace").exists()


def test_config_commands_edges(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "init", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["config", "init", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "already exists" in result.output
    result = runner.invoke(app, ["config", "init", "--root", str(tmp_path), "--force"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["config", "show", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "pal config" in result.output


def test_config_apply_dict_legacy_shapes(tmp_path: Path) -> None:
    cfg = PalConfig(root=tmp_path, worktree_root=Path("_wt"))
    _apply_dict(
        cfg,
        {
            "root": "root",
            "worktree_root": "worktrees",
            "branch_prefix": "bug",
            "repos": ["a", 2],
            "editor": "code",
            "agent": {"add_dir": ["a", 2]},
            "codex": {
                "sandbox": "read-only",
                "approval": "never",
                "full_auto": True,
                "add_dir": ["c", 3],
            },
            "claude": {
                "permission_mode": "plan",
                "model": "opus",
                "allow_bypass_permissions": True,
                "extra_args": ["--x", 1],
                "add_dir": ["d", 4],
            },
            "local_files": {
                "enabled": True,
                "overwrite": True,
                "paths": [".env", 5],
                "patterns": ["*.local", 6],
                "repos": {"repo1": [".env"], "repo2": {"paths": ["a"], "patterns": ["b"]}},
            },
        },
    )
    assert cfg.root == Path("root")
    assert cfg.worktree_root == Path("worktrees")
    assert cfg.branch_prefix == "bug"
    assert cfg.repos == ["a", "2"]
    assert cfg.editor == "code"
    assert cfg.agent.add_dirs == ["a", "2"]
    assert cfg.codex.add_dirs == ["c", "3"]
    assert cfg.claude.add_dirs == ["d", "4"]
    assert cfg.local_files.repos["repo1"].paths == [".env"]
    assert cfg.local_files.repos["repo2"].patterns == ["b"]

    cfg2 = PalConfig(root=tmp_path, worktree_root=Path("_wt"))
    _apply_dict(
        cfg2, {"agent": {"add_dir": "a"}, "codex": {"add_dir": "b"}, "claude": {"add_dir": "c"}}
    )
    assert cfg2.agent.add_dirs == ["a"]
    assert cfg2.codex.add_dirs == ["b"]
    assert cfg2.claude.add_dirs == ["c"]

    cfg3 = PalConfig(root=tmp_path, worktree_root=Path("_wt"))
    _apply_dict(
        cfg3,
        {
            "agent": {"add_dir": 1},
            "codex": {"add_dir": 2},
            "claude": {"add_dir": 3},
            "local_files": {"repos": {"ignored": 1, "empty": {}}},
        },
    )
    assert cfg3.agent.add_dirs == []
    assert cfg3.codex.add_dirs == []
    assert cfg3.claude.add_dirs == []
    assert cfg3.local_files.repos["empty"].paths == []


def test_load_config_global_and_absolute_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    global_path = config_home / "config.toml"
    global_path.write_text('branch_prefix = "global"\n', encoding="utf-8")
    monkeypatch.setattr("pal.config.global_config_path", lambda: global_path)
    monkeypatch.setattr("pal.config.legacy_global_config_path", lambda: tmp_path / "missing.toml")
    cfg = load_config(
        root=tmp_path,
        cli_overrides={"root": str(tmp_path), "worktree_root": str(tmp_path / "absolute-wt")},
    )
    assert cfg.branch_prefix == "global"
    assert cfg.worktree_root == tmp_path / "absolute-wt"


def test_completion_error_and_empty_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path, "worktree_root": tmp_path / "missing", "branch_prefix": "bug"}
    assert complete_feature(ctx, [], "") == []

    monkeypatch.setattr(
        "pal.completion.load_config", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError())
    )
    assert complete_feature(ctx, [], "") == []
    assert complete_repo(ctx, [], "") == []
    assert complete_repo_in_feature(ctx, [], "") == []

    monkeypatch.setattr("pal.completion.AGENTS", None)
    assert complete_agent(ctx, [], "") == []

    monkeypatch.undo()
    ctx.params = {"root": tmp_path}
    assert complete_repo_in_feature(ctx, [], "") == []
    ctx.params = {"root": tmp_path, "feature": "missing"}
    assert complete_repo_in_feature(ctx, [], "") == []


def test_local_files_edge_cases(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("x\n", encoding="utf-8")
    (src / "dir").mkdir()
    (src / "file.txt").write_text("x\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (src / "link.txt").symlink_to(outside)

    resolved, invalid = resolve_local_file_paths(
        src,
        paths=["file.txt", "file.txt", ".git/config", "../bad"],
        patterns=["*", "/abs"],
    )
    assert ".git/config" not in resolved
    assert "file.txt" in resolved
    assert resolved.count("file.txt") == 1
    assert set(invalid) == {"../bad", "/abs"}

    result = copy_local_files(
        src,
        dst,
        paths=["missing.txt", ".git/config", "link.txt"],
        overwrite=False,
    )
    assert result.skipped_missing == [Path("missing.txt")]
    assert set(result.skipped_invalid) == {".git/config", "link.txt"}

    class OutsideMatch:
        def relative_to(self, _base):  # noqa: ANN001
            raise ValueError("outside")

    class FakeSource:
        def resolve(self) -> Path:
            return src.resolve()

        def glob(self, _pattern: str):  # noqa: ANN201
            return [OutsideMatch()]

    resolved, invalid = resolve_local_file_paths(FakeSource(), patterns=["*"])  # type: ignore[arg-type]
    assert resolved == []
    assert invalid == []


def test_git_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        run_calls.append(cmd)
        if "fail" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda exe: f"/bin/{exe}")
    assert git_module.exists_on_path("git") is True
    monkeypatch.setattr("shutil.which", lambda _exe: None)
    assert git_module.exists_on_path("missing") is False
    assert git_module.run(["ok"]) == "out"
    assert git_module.is_git_repo(tmp_path) is True
    assert git_module.branch_exists(tmp_path, "main") is True
    git_module.worktree_remove(tmp_path, tmp_path / "wt", force=False)
    assert run_calls[-1][-1] == str(tmp_path / "wt")
    git_module.worktree_remove(tmp_path, tmp_path / "wt", force=True)
    assert "-f" in run_calls[-1]
    assert git_module.git_status_short(tmp_path) == "out"
    assert git_module.git_porcelain(tmp_path) == "out"

    def failing_run(cmd, **_kwargs):  # noqa: ANN001
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(git_module.subprocess, "run", failing_run)
    assert git_module.is_git_repo(tmp_path) is False
    assert git_module.branch_exists(tmp_path, "main") is False
    assert git_module.git_diff_stat(tmp_path) == ""

    monkeypatch.setattr(git_module, "is_git_repo", lambda p: p.name == "repo")
    for name in ["_wt", ".git", ".venv", "node_modules", "repo", "notrepo"]:
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / "file.txt").write_text("x\n", encoding="utf-8")
    assert git_module.list_child_repos(tmp_path) == ["repo"]


def test_codex_and_claude_command_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAL_TEST_DIR", str(tmp_path / "cache"))
    codex_cmd = codex_module.codex_cmd(
        tmp_path,
        codex=CodexConfig(add_dirs=["", "$PAL_TEST_DIR", "relative"]),
    )
    assert codex_cmd.count("--add-dir") == 2
    assert codex_cmd[-1].endswith("relative")

    claude_cmd = claude_module.claude_cmd(tmp_path, add_dirs=["", "$PAL_TEST_DIR", "relative"])
    assert claude_cmd.count("--add-dir") == 2

    calls: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, check=True, cwd=None):  # noqa: ANN001
        assert check is True
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(codex_module.subprocess, "run", fake_run)
    codex_module.run_interactive(tmp_path, CodexConfig(), extra_args=["status"])
    monkeypatch.setattr(claude_module.subprocess, "run", fake_run)
    claude_module.run_interactive(tmp_path, extra_args=["status"])
    assert calls[0][0][0] == "codex"
    assert calls[1] == (["claude", "status"], str(tmp_path))


def test_write_code_workspace_no_repos_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "not-repo").mkdir()
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda _p: False)
    ws = write_code_workspace(tmp_path, "feat")
    assert '"folders": []' in ws.read_text(encoding="utf-8")
