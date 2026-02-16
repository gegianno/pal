from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


def _normalize_add_dir(workspace_dir: Path, raw: str) -> str:
    expanded = os.path.expandvars(raw)
    p = Path(expanded).expanduser()
    if not p.is_absolute():
        p = workspace_dir / p
    return str(p.resolve())


def claude_cmd(
    workspace_dir: Path,
    *,
    add_dirs: Optional[list[str]] = None,
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    cmd = ["claude"]

    for raw_dir in add_dirs or []:
        raw_dir = str(raw_dir).strip()
        if not raw_dir:
            continue
        cmd += ["--add-dir", _normalize_add_dir(workspace_dir, raw_dir)]

    if extra_args:
        cmd += extra_args
    return cmd


def run_interactive(
    workspace_dir: Path,
    *,
    add_dirs: Optional[list[str]] = None,
    extra_args: Optional[list[str]] = None,
) -> None:
    subprocess.run(
        claude_cmd(workspace_dir, add_dirs=add_dirs, extra_args=extra_args),
        check=True,
        cwd=str(workspace_dir),
    )
