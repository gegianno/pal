from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import load_config


def cfg_from_options(
    root: Path,
    worktree_root: Optional[Path],
    branch_prefix: Optional[str],
):
    overrides = {"root": str(root)}
    if worktree_root is not None:
        overrides["worktree_root"] = str(worktree_root)
    if branch_prefix is not None:
        overrides["branch_prefix"] = branch_prefix
    return load_config(root=root, cli_overrides=overrides)
