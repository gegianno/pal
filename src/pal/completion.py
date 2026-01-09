from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from .config import load_config
from .git import is_git_repo, list_child_repos


def _cfg_from_ctx(ctx: click.Context):
    params: dict[str, Any] = dict(getattr(ctx, "params", {}) or {})
    root = params.get("root") or Path(".")
    worktree_root = params.get("worktree_root")
    branch_prefix = params.get("branch_prefix")

    overrides: dict[str, Any] = {"root": str(root)}
    if worktree_root is not None:
        overrides["worktree_root"] = str(worktree_root)
    if branch_prefix is not None:
        overrides["branch_prefix"] = str(branch_prefix)
    return load_config(root=Path(root), cli_overrides=overrides)


def _starts_with(name: str, incomplete: str) -> bool:
    return name.startswith(incomplete or "")


def complete_feature(ctx: click.Context, args: list[str], incomplete: str):
    try:
        cfg = _cfg_from_ctx(ctx)
        if not cfg.worktree_root.exists():
            return []
        return sorted([p.name for p in cfg.worktree_root.iterdir() if p.is_dir() and _starts_with(p.name, incomplete)])
    except Exception:
        return []


def complete_repo(ctx: click.Context, args: list[str], incomplete: str):
    try:
        cfg = _cfg_from_ctx(ctx)
        repos = cfg.repos if cfg.repos else list_child_repos(cfg.root)
        return sorted([r for r in repos if _starts_with(r, incomplete)])
    except Exception:
        return []


def complete_repo_in_feature(ctx: click.Context, args: list[str], incomplete: str):
    try:
        cfg = _cfg_from_ctx(ctx)
        feature = (ctx.params or {}).get("feature")
        if not feature:
            return []
        feature_dir = cfg.worktree_root / str(feature)
        if not feature_dir.exists():
            return []
        repos = [p.name for p in feature_dir.iterdir() if p.is_dir() and is_git_repo(p)]
        return sorted([r for r in repos if _starts_with(r, incomplete)])
    except Exception:
        return []
