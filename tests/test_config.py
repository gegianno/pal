from __future__ import annotations

from pathlib import Path

import pytest

from pal.config import load_config
from pal.cli import app

try:
    import tomllib  # py>=3.11
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def test_load_config_relative_root_does_not_duplicate_worktree_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "projects").mkdir()

    cfg = load_config(root=Path("projects"), cli_overrides={"root": "projects"})

    assert cfg.root == (tmp_path / "projects").resolve()
    assert cfg.worktree_root == (tmp_path / "projects" / "_wt").resolve()


def test_config_init_writes_valid_toml(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["config", "init", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    ws_local_path = tmp_path / ".pal.toml"
    assert ws_local_path.exists()

    parsed = tomllib.loads(ws_local_path.read_text(encoding="utf-8"))
    assert parsed["root"] == "."
    assert parsed["worktree_root"] == "_wt"
    assert parsed["branch_prefix"] == "feat"
    assert parsed["codex"]["sandbox"] == "workspace-write"
    assert parsed["codex"]["approval"] == "on-request"
    assert parsed["codex"]["full_auto"] is False
    assert parsed["claude"]["permission_mode"] == "acceptEdits"
    assert parsed["claude"]["allow_bypass_permissions"] is False


def test_load_config_parses_local_files(tmp_path: Path) -> None:
    (tmp_path / ".pal.toml").write_text(
        'root = "."\n'
        "\n"
        "[local_files]\n"
        "enabled = true\n"
        "overwrite = false\n"
        'paths = [".env"]\n'
        'patterns = ["**/.npmrc"]\n'
        "\n"
        "[local_files.repos.integrations]\n"
        'paths = ["apps/searcher/collections/.env"]\n'
        'patterns = ["apps/**/.npmrc"]\n',
        encoding="utf-8",
    )

    cfg = load_config(root=tmp_path, cli_overrides={"root": str(tmp_path)})
    assert cfg.local_files.enabled is True
    assert cfg.local_files.overwrite is False
    assert cfg.local_files.paths == [".env"]
    assert cfg.local_files.patterns == ["**/.npmrc"]
    assert cfg.local_files.repos["integrations"].paths == ["apps/searcher/collections/.env"]
    assert cfg.local_files.repos["integrations"].patterns == ["apps/**/.npmrc"]


def test_load_config_parses_codex_add_dirs(tmp_path: Path) -> None:
    (tmp_path / ".pal.toml").write_text(
        'root = "."\n\n[codex]\nadd_dirs = ["/tmp/a", "/tmp/b"]\n',
        encoding="utf-8",
    )
    cfg = load_config(root=tmp_path, cli_overrides={"root": str(tmp_path)})
    assert cfg.codex.add_dirs == ["/tmp/a", "/tmp/b"]


def test_load_config_parses_agent_add_dirs(tmp_path: Path) -> None:
    (tmp_path / ".pal.toml").write_text(
        'root = "."\n\n[agent]\nadd_dirs = ["/tmp/a", "/tmp/b"]\n',
        encoding="utf-8",
    )
    cfg = load_config(root=tmp_path, cli_overrides={"root": str(tmp_path)})
    assert cfg.agent.add_dirs == ["/tmp/a", "/tmp/b"]


def test_load_config_parses_claude_section(tmp_path: Path) -> None:
    (tmp_path / ".pal.toml").write_text(
        (
            'root = "."\n\n'
            "[claude]\n"
            'permission_mode = "plan"\n'
            'model = "sonnet"\n'
            'add_dirs = ["/tmp/a"]\n'
            'extra_args = ["--foo", "bar"]\n'
            "allow_bypass_permissions = true\n"
        ),
        encoding="utf-8",
    )
    cfg = load_config(root=tmp_path, cli_overrides={"root": str(tmp_path)})
    assert cfg.claude.permission_mode == "plan"
    assert cfg.claude.model == "sonnet"
    assert cfg.claude.add_dirs == ["/tmp/a"]
    assert cfg.claude.extra_args == ["--foo", "bar"]
    assert cfg.claude.allow_bypass_permissions is True


def test_rm_removes_empty_feature_dir(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    (tmp_path / "_wt" / "property-assignment").mkdir(parents=True)

    result = runner.invoke(app, ["rm", "property-assignment", "--root", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "_wt" / "property-assignment").exists()


def test_rm_removes_feature_dir_with_non_repo_dirs(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    feature_dir = tmp_path / "_wt" / "property-assign"
    (feature_dir / "_wt").mkdir(parents=True)

    result = runner.invoke(app, ["rm", "property-assign", "--root", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert not feature_dir.exists()


def test_version_flag_prints_version(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("pal ")
