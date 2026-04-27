from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pal.claude as claude_module
import pal.codex as codex_module
from pal.config import CodexConfig


def test_codex_and_claude_add_dir_args_skip_empty_and_expand_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAL_TEST_DIR", str(tmp_path / "cache"))

    codex_cmd = codex_module.codex_cmd(
        tmp_path,
        codex=CodexConfig(add_dirs=["", "$PAL_TEST_DIR", "relative"]),
    )
    claude_cmd = claude_module.claude_cmd(tmp_path, add_dirs=["", "$PAL_TEST_DIR", "relative"])

    assert codex_cmd.count("--add-dir") == 2
    assert codex_cmd[-1].endswith("relative")
    assert claude_cmd.count("--add-dir") == 2


def test_agent_run_interactive_invokes_provider_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
