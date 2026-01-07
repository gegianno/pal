from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


EXCLUDED_DIR_PARTS = {
    ".git",
    "_wt",
    "node_modules",
    ".venv",
    "dist",
    "build",
    ".next",
    "target",
    ".aws-sam",
    ".cache",
}


@dataclass(frozen=True)
class CopyResult:
    copied: list[Path]
    skipped_missing: list[Path]
    skipped_existing: list[Path]
    skipped_invalid: list[str]


def _safe_relative_path(rel: str) -> Optional[Path]:
    p = Path(rel)
    if p.is_absolute():
        return None
    if any(part == ".." for part in p.parts):
        return None
    return p


def _is_excluded(rel: Path) -> bool:
    return any(part in EXCLUDED_DIR_PARTS for part in rel.parts)


def resolve_local_file_paths(
    source_repo_dir: Path,
    *,
    paths: Optional[list[str]] = None,
    patterns: Optional[list[str]] = None,
) -> tuple[list[str], list[str]]:
    """
    Resolve a mix of explicit repo-relative paths and glob patterns into concrete repo-relative paths.

    Returns (resolved_paths, skipped_invalid_specs).
    """
    resolved: list[str] = []
    skipped_invalid: list[str] = []

    def add_resolved(rel: Path) -> None:
        if _is_excluded(rel):
            return
        s = rel.as_posix()
        if s not in resolved:
            resolved.append(s)

    for rel in paths or []:
        safe_rel = _safe_relative_path(rel)
        if safe_rel is None:
            skipped_invalid.append(rel)
            continue
        add_resolved(safe_rel)

    repo_root = source_repo_dir.resolve()

    for pat in patterns or []:
        safe_pat = _safe_relative_path(pat)
        if safe_pat is None:
            skipped_invalid.append(pat)
            continue
        pat_str = safe_pat.as_posix()

        matches = []
        for m in source_repo_dir.glob(pat_str):
            try:
                rel = m.relative_to(source_repo_dir)
            except Exception:
                continue
            if _is_excluded(rel):
                continue
            if not m.is_file():
                continue
            # Avoid copying files that resolve outside the repo via symlinks.
            try:
                m.resolve().relative_to(repo_root)
            except Exception:
                continue
            matches.append(rel)

        for rel in sorted(matches, key=lambda p: p.as_posix()):
            add_resolved(rel)

    return resolved, skipped_invalid


def copy_local_files(
    source_repo_dir: Path,
    dest_worktree_dir: Path,
    *,
    paths: list[str],
    overwrite: bool = False,
) -> CopyResult:
    copied: list[Path] = []
    skipped_missing: list[Path] = []
    skipped_existing: list[Path] = []
    skipped_invalid: list[str] = []
    repo_root = source_repo_dir.resolve()

    for rel in paths:
        safe_rel = _safe_relative_path(rel)
        if safe_rel is None:
            skipped_invalid.append(rel)
            continue
        if _is_excluded(safe_rel):
            skipped_invalid.append(rel)
            continue

        src = source_repo_dir / safe_rel
        if not src.exists() or not src.is_file():
            skipped_missing.append(safe_rel)
            continue
        # Avoid copying files that resolve outside the repo via symlinks.
        try:
            src.resolve().relative_to(repo_root)
        except Exception:
            skipped_invalid.append(rel)
            continue

        dst = dest_worktree_dir / safe_rel
        if dst.exists() and not overwrite:
            skipped_existing.append(safe_rel)
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(safe_rel)

    return CopyResult(
        copied=copied,
        skipped_missing=skipped_missing,
        skipped_existing=skipped_existing,
        skipped_invalid=skipped_invalid,
    )
