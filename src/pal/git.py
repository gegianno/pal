from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, Optional


def run(cmd: list[str], cwd: Optional[Path] = None) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return p.stdout.strip()


def exists_on_path(exe: str) -> bool:
    import shutil
    return shutil.which(exe) is not None


def is_git_repo(path: Path) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return True
    except Exception:
        return False


def list_child_repos(root: Path) -> list[str]:
    repos: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if name in {"_wt", ".git", ".venv", "node_modules"}:
            continue
        if is_git_repo(child):
            repos.append(name)
    return sorted(repos)


def branch_exists(repo_path: Path, branch: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return True
    except Exception:
        return False


def worktree_add(repo_path: Path, worktree_path: Path, branch: str, create: bool) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(repo_path), "worktree", "add"]
    if create:
        # `git worktree add -b <new-branch> <path> [<start-point>]`
        cmd += ["-b", branch, str(worktree_path)]
    else:
        cmd += [str(worktree_path), branch]
    subprocess.run(cmd, check=True)


def worktree_remove(repo_path: Path, worktree_path: Path, force: bool = True) -> None:
    cmd = ["git", "-C", str(repo_path), "worktree", "remove"]
    if force:
        cmd.append("-f")
    cmd.append(str(worktree_path))
    subprocess.run(cmd, check=True)


def git_status_short(repo_path: Path) -> str:
    return run(["git", "-C", str(repo_path), "status", "-sb"])


def git_diff_stat(repo_path: Path) -> str:
    # quick summary for table display
    try:
        return run(["git", "-C", str(repo_path), "diff", "--stat"])
    except subprocess.CalledProcessError:
        return ""


def git_porcelain(repo_path: Path) -> str:
    return run(["git", "-C", str(repo_path), "status", "--porcelain"])
