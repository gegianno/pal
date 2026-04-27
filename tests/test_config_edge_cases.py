from __future__ import annotations

from pathlib import Path

import pytest

from pal.config import PalConfig, _apply_dict, load_config


def test_apply_dict_accepts_legacy_and_mixed_value_shapes(tmp_path: Path) -> None:
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


def test_apply_dict_accepts_scalar_legacy_add_dir(tmp_path: Path) -> None:
    cfg = PalConfig(root=tmp_path, worktree_root=Path("_wt"))
    _apply_dict(
        cfg, {"agent": {"add_dir": "a"}, "codex": {"add_dir": "b"}, "claude": {"add_dir": "c"}}
    )
    assert cfg.agent.add_dirs == ["a"]
    assert cfg.codex.add_dirs == ["b"]
    assert cfg.claude.add_dirs == ["c"]


def test_apply_dict_ignores_invalid_add_dir_and_repo_shapes(tmp_path: Path) -> None:
    cfg = PalConfig(root=tmp_path, worktree_root=Path("_wt"))
    _apply_dict(
        cfg,
        {
            "agent": {"add_dir": 1},
            "codex": {"add_dir": 2},
            "claude": {"add_dir": 3},
            "local_files": {"repos": {"ignored": 1, "empty": {}}},
        },
    )
    assert cfg.agent.add_dirs == []
    assert cfg.codex.add_dirs == []
    assert cfg.claude.add_dirs == []
    assert cfg.local_files.repos["empty"].paths == []


def test_load_config_applies_global_config_before_absolute_cli_worktree(
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
