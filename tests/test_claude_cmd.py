from __future__ import annotations

from pathlib import Path

from pal.claude import claude_cmd


def test_claude_cmd_allows_extra_args_passthrough() -> None:
    cmd = claude_cmd(
        Path("/workspace"),
        extra_args=["--permission-mode", "plan", "Audit this codebase"],
    )
    assert cmd[0] == "claude"
    assert "--permission-mode" in cmd


def test_claude_cmd_add_dirs_adds_add_dir_flags() -> None:
    cmd = claude_cmd(
        Path("/workspace"),
        add_dirs=["/tmp/a", "/tmp/b"],
    )
    assert cmd[0] == "claude"
    assert cmd.count("--add-dir") == 2
