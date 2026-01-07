from __future__ import annotations

from pathlib import Path

import pytest

from wtfa.config import load_config
from wtfa.cli import app

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

    ws_local_path = tmp_path / ".wtfa.toml"
    assert ws_local_path.exists()

    parsed = tomllib.loads(ws_local_path.read_text(encoding="utf-8"))
    assert parsed["root"] == "."
    assert parsed["worktree_root"] == "_wt"
    assert parsed["branch_prefix"] == "feat"
    assert parsed["codex"]["sandbox"] == "workspace-write"
    assert parsed["codex"]["approval"] == "on-request"
    assert parsed["codex"]["full_auto"] is False
