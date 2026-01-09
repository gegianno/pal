from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .config import CodexConfig


def _normalize_add_dir(workspace_dir: Path, raw: str) -> str:
    expanded = os.path.expandvars(raw)
    p = Path(expanded).expanduser()
    if not p.is_absolute():
        p = workspace_dir / p
    return str(p.resolve())


def codex_cmd(
    workspace_dir: Path,
    prompt: Optional[str] = None,
    *,
    extra_args: Optional[list[str]] = None,
    non_interactive: bool = False,
    codex: CodexConfig,
) -> list[str]:
    """
    Build a Codex command that is *scoped to this workspace directory*.

    Uses per-invocation flags so it does NOT modify ~/.codex/config.toml defaults.

    - --cd sets the working directory (workspace root)
    - --sandbox workspace-write restricts writes to the workspace
    - --ask-for-approval sets approval policy
    - --add-dir adds extra writable roots (optional, repeatable)
    - --full-auto (optional) mirrors Codex CLI shortcut: approval on-request + workspace-write
    """
    cmd = ["codex", "--cd", str(workspace_dir)]

    if codex.full_auto:
        cmd += ["--full-auto"]
    else:
        cmd += ["--sandbox", codex.sandbox, "--ask-for-approval", codex.approval]

    for raw_dir in codex.add_dirs:
        raw_dir = str(raw_dir).strip()
        if not raw_dir:
            continue
        cmd += ["--add-dir", _normalize_add_dir(workspace_dir, raw_dir)]

    if extra_args:
        cmd += extra_args
    else:
        if non_interactive:
            cmd += ["exec"]
            # For exec mode, prompt is required
            if prompt is None:
                raise ValueError("prompt is required for exec")
            cmd.append(prompt)
        else:
            if prompt:
                cmd.append(prompt)
    return cmd


def run_interactive(
    workspace_dir: Path, codex_cfg: CodexConfig, *, extra_args: Optional[list[str]] = None
) -> None:
    subprocess.run(
        codex_cmd(workspace_dir, non_interactive=False, codex=codex_cfg, extra_args=extra_args),
        check=True,
    )


def run_exec(workspace_dir: Path, prompt: str, codex_cfg: CodexConfig) -> None:
    subprocess.run(
        codex_cmd(workspace_dir, prompt, non_interactive=True, codex=codex_cfg), check=True
    )
