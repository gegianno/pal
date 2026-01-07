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
