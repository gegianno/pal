from __future__ import annotations

from pathlib import Path

from wtfa.codex import codex_cmd
from wtfa.config import CodexConfig


def test_codex_cmd_full_auto_is_flag() -> None:
    cmd = codex_cmd(
        Path("."),
        prompt="hi",
        non_interactive=True,
        codex=CodexConfig(full_auto=True),
    )
    assert "--full-auto" in cmd
    assert "true" not in cmd


def test_codex_cmd_allows_extra_args_passthrough() -> None:
    cmd = codex_cmd(
        Path("/workspace"),
        extra_args=["resume", "019b947b-ff0f-7ff3-8a49-4723ee751f20"],
        codex=CodexConfig(full_auto=False, sandbox="workspace-write", approval="on-request"),
    )
    assert cmd[:3] == ["codex", "--cd", "/workspace"]
    assert "resume" in cmd
