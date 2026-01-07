from __future__ import annotations

from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .config import load_config, global_config_path
from .git import (
    exists_on_path,
    is_git_repo,
    list_child_repos,
    branch_exists,
    worktree_add,
    worktree_remove,
    git_status_short,
    git_porcelain,
)
from .vscode import write_code_workspace
from .codex import run_interactive, run_exec
from .local_files import copy_local_files, resolve_local_file_paths

app = typer.Typer(
    add_completion=True,
    help="Pal: multi-repo feature workspaces using git worktrees (Codex-friendly).",
    no_args_is_help=True,
)
console = Console()


def _detect_editor(preferred: str) -> str:
    if preferred:
        return preferred
    if exists_on_path("cursor"):
        return "cursor"
    if exists_on_path("code"):
        return "code"
    return ""


def _feature_dir(cfg, feature: str) -> Path:
    return cfg.worktree_root / feature


def _worktree_path(cfg, feature: str, repo: str) -> Path:
    return _feature_dir(cfg, feature) / repo


def _branch(cfg, feature: str) -> str:
    return f"{cfg.branch_prefix}/{feature}"


def _require_root_repo(cfg, repo: str) -> Path:
    repo_path = cfg.root / repo
    if not repo_path.exists():
        raise typer.BadParameter(f"Repo '{repo}' not found under root '{cfg.root}'.")
    if not is_git_repo(repo_path):
        raise typer.BadParameter(f"'{repo_path}' is not a git repo.")
    return repo_path


@app.callback()
def main():
    """
    pal creates per-feature multi-repo workspaces using git worktrees.
    It also generates VS Code / Cursor multi-root workspaces per feature.
    """


def _cfg_from_ctx(
    root: Path,
    worktree_root: Optional[Path],
    branch_prefix: Optional[str],
):
    overrides = {}
    overrides["root"] = str(root)
    if worktree_root is not None:
        overrides["worktree_root"] = str(worktree_root)
    if branch_prefix is not None:
        overrides["branch_prefix"] = branch_prefix
    return load_config(root=root, cli_overrides=overrides)


@app.command()
def doctor(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Check prerequisites and show config discovery paths."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    ok = True

    def chk(exe: str, label: str):
        nonlocal ok
        if exists_on_path(exe):
            console.print(f"[green]✓[/green] {label}: {exe}")
        else:
            ok = False
            console.print(f"[red]✗[/red] {label}: {exe} (not found in PATH)")

    chk("git", "Git")
    chk("codex", "Codex CLI")
    if exists_on_path("cursor") or exists_on_path("code"):
        chk("cursor" if exists_on_path("cursor") else "code", "Editor (auto-detected)")
    else:
        console.print("[yellow]•[/yellow] Editor: cursor/code not found (pal open will still write .code-workspace)")

    console.print()
    console.print(Panel.fit(
        f"[bold]Resolved config[/bold]\n"
        f"root: {cfg.root}\n"
        f"worktree_root: {cfg.worktree_root}\n"
        f"branch_prefix: {cfg.branch_prefix}\n"
        f"global config: {global_config_path()}\n"
        f"local config: {cfg.local_config_path}",
        title="pal",
    ))

    raise typer.Exit(code=0 if ok else 1)


@app.command()
def repos(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """List git repos under the projects root (or config allowlist, if set)."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)

    repos = cfg.repos if cfg.repos else list_child_repos(cfg.root)
    if not repos:
        console.print("[yellow]No repos found.[/yellow] Put repos under root or set repos=[...] in .pal.toml.")
        raise typer.Exit(code=1)

    table = Table(title="Repos", show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Path")
    for r in repos:
        table.add_row(r, str((cfg.root / r)))
    console.print(table)


@app.command()
def ls(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """List feature workspaces under worktree_root."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    cfg.worktree_root.mkdir(parents=True, exist_ok=True)

    features = sorted([p.name for p in cfg.worktree_root.iterdir() if p.is_dir()])
    if not features:
        console.print("[yellow]No feature workspaces yet.[/yellow] Try: pal new <feature> <repo...>")
        return

    table = Table(title="Feature workspaces", header_style="bold")
    table.add_column("Feature")
    table.add_column("Path")
    for f in features:
        table.add_row(f, str(cfg.worktree_root / f))
    console.print(table)


def _ensure_worktree(cfg, feature: str, repo: str) -> None:
    repo_path = _require_root_repo(cfg, repo)
    wt_path = _worktree_path(cfg, feature, repo)
    if wt_path.exists():
        console.print(f"[green]✓[/green] exists: {feature}/{repo}")
        return

    b = _branch(cfg, feature)
    create = not branch_exists(repo_path, b)
    console.print(f"[cyan]+[/cyan] worktree add {feature}/{repo} → {wt_path} (branch {b}{' [new]' if create else ''})")
    worktree_add(repo_path, wt_path, b, create=create)


def _refresh_workspace(cfg, feature: str) -> Path:
    feature_dir = _feature_dir(cfg, feature)
    feature_dir.mkdir(parents=True, exist_ok=True)
    ws_path = write_code_workspace(feature_dir, feature)
    return ws_path


def _sync_local_files(cfg, feature: str, repo: str, *, overwrite: bool) -> None:
    repo_path = _require_root_repo(cfg, repo)
    wt_path = _worktree_path(cfg, feature, repo)
    repo_cfg = cfg.local_files.repos.get(repo)

    resolved_paths, skipped_invalid = resolve_local_file_paths(
        repo_path,
        paths=list(cfg.local_files.paths) + (list(repo_cfg.paths) if repo_cfg else []),
        patterns=list(cfg.local_files.patterns) + (list(repo_cfg.patterns) if repo_cfg else []),
    )

    if not resolved_paths and not skipped_invalid:
        return

    result = copy_local_files(repo_path, wt_path, paths=resolved_paths, overwrite=overwrite)
    if result.copied:
        console.print(f"[green]✓[/green] copied local files for {repo}: {len(result.copied)}")
    if result.skipped_existing:
        console.print(f"[yellow]•[/yellow] skipped existing in worktree for {repo}: {len(result.skipped_existing)}")
    invalid_count = len(result.skipped_invalid) + len(skipped_invalid)
    if invalid_count:
        console.print(f"[yellow]•[/yellow] skipped invalid specs for {repo}: {invalid_count}")


@app.command()
def new(
    feature: str = typer.Argument(..., help="Feature workspace name (folder under worktree_root)."),
    repos: List[str] = typer.Argument(..., help="One or more repo folder names under root."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
    copy_local: Optional[bool] = typer.Option(
        None,
        "--copy-local/--no-copy-local",
        help="Copy local (uncommitted) files into new worktrees (default: config).",
    ),
    overwrite_local: Optional[bool] = typer.Option(
        None,
        "--overwrite-local/--no-overwrite-local",
        help="Overwrite existing local files in the worktree when copying (default: config).",
    ),
):
    """Create a new feature workspace with worktrees for the given repos."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    do_copy = cfg.local_files.enabled if copy_local is None else copy_local
    do_overwrite = cfg.local_files.overwrite if overwrite_local is None else overwrite_local
    for r in repos:
        _ensure_worktree(cfg, feature, r)
        if do_copy:
            _sync_local_files(cfg, feature, r, overwrite=do_overwrite)
    ws_path = _refresh_workspace(cfg, feature)
    console.print(f"[green]✓[/green] workspace: {ws_path}")


@app.command()
def add(
    feature: str = typer.Argument(..., help="Existing feature workspace name."),
    repos: List[str] = typer.Argument(..., help="One or more repos to add to the feature workspace."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
    copy_local: Optional[bool] = typer.Option(
        None,
        "--copy-local/--no-copy-local",
        help="Copy local (uncommitted) files into worktrees (default: config).",
    ),
    overwrite_local: Optional[bool] = typer.Option(
        None,
        "--overwrite-local/--no-overwrite-local",
        help="Overwrite existing local files in the worktree when copying (default: config).",
    ),
):
    """Add more repos (worktrees) to an existing feature workspace."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    do_copy = cfg.local_files.enabled if copy_local is None else copy_local
    do_overwrite = cfg.local_files.overwrite if overwrite_local is None else overwrite_local
    for r in repos:
        _ensure_worktree(cfg, feature, r)
        if do_copy:
            _sync_local_files(cfg, feature, r, overwrite=do_overwrite)
    ws_path = _refresh_workspace(cfg, feature)
    console.print(f"[green]✓[/green] updated workspace: {ws_path}")
    console.print("[yellow]Tip:[/yellow] Restart an interactive Codex session if it's already running.")


@app.command()
def status(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Show git status summary for each repo worktree in the feature workspace."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")

    table = Table(title=f"Status: {feature}", header_style="bold")
    table.add_column("Repo")
    table.add_column("Branch")
    table.add_column("Dirty")
    table.add_column("Path")

    for child in sorted(feature_dir.iterdir(), key=lambda p: p.name):
        if not (child.is_dir() and is_git_repo(child)):
            continue
        status = git_status_short(child)
        branch = status.splitlines()[0].replace("## ", "").strip()
        dirty = "yes" if git_porcelain(child) else "no"
        table.add_row(child.name, branch, dirty, str(child))
    console.print(table)


@app.command()
def open(
    feature: str = typer.Argument(...),
    editor: str = typer.Option("", "--editor", help="Force editor command (cursor|code). Auto-detect if empty."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Open the feature workspace in Cursor/VS Code (multi-root). Writes the .code-workspace file."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")

    ws_path = _refresh_workspace(cfg, feature)
    ed = _detect_editor(editor or cfg.editor)
    if not ed:
        console.print(f"[green]✓[/green] wrote workspace: {ws_path}")
        console.print("[yellow]Editor not found.[/yellow] Install Cursor ('cursor') or VS Code ('code') CLI to auto-open.")
        return

    console.print(f"[cyan]→[/cyan] opening in {ed}: {ws_path}")
    import subprocess
    subprocess.Popen([ed, str(ws_path)])  # intentionally not check=True (editor exits immediately)
    console.print("[green]✓[/green] opened")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def codex(
    ctx: typer.Context,
    feature: str = typer.Argument(...),
    full_auto: Optional[bool] = typer.Option(None, "--full-auto", help="Pass --full-auto to Codex (overrides config)."),
    sandbox: Optional[str] = typer.Option(None, "--sandbox", help="Codex sandbox policy override."),
    approval: Optional[str] = typer.Option(None, "--approval", help="Codex approval policy override."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """
    Run interactive Codex in the feature workspace root, *scoped to this directory*.

    Default behavior does not touch your global ~/.codex/config.toml; it uses per-run flags.
    """
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")

    # per-run overrides
    if full_auto is not None:
        cfg.codex.full_auto = full_auto
    if sandbox is not None:
        cfg.codex.sandbox = sandbox
    if approval is not None:
        cfg.codex.approval = approval

    console.print(Panel.fit(
        f"workspace: {feature_dir}\n"
        f"codex: sandbox={cfg.codex.sandbox} approval={cfg.codex.approval} full_auto={cfg.codex.full_auto}",
        title="Launching Codex",
    ))
    extra = list(ctx.args)
    run_interactive(feature_dir, cfg.codex, extra_args=extra if extra else None)


@app.command()
def exec(
    feature: str = typer.Argument(...),
    prompt: str = typer.Argument(..., help="Prompt to run in non-interactive mode."),
    full_auto: Optional[bool] = typer.Option(None, "--full-auto", help="Pass --full-auto to Codex (overrides config)."),
    sandbox: Optional[str] = typer.Option(None, "--sandbox", help="Codex sandbox policy override."),
    approval: Optional[str] = typer.Option(None, "--approval", help="Codex approval policy override."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Run non-interactive 'codex exec' in the feature workspace."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")

    if full_auto is not None:
        cfg.codex.full_auto = full_auto
    if sandbox is not None:
        cfg.codex.sandbox = sandbox
    if approval is not None:
        cfg.codex.approval = approval

    console.print(Panel.fit(
        f"workspace: {feature_dir}\n"
        f"codex: sandbox={cfg.codex.sandbox} approval={cfg.codex.approval} full_auto={cfg.codex.full_auto}\n"
        f"prompt: {prompt}",
        title="Running Codex exec",
    ))
    run_exec(feature_dir, prompt, cfg.codex)


@app.command()
def rm(
    feature: str = typer.Argument(...),
    repos: List[str] = typer.Option(
        [],
        "--repo",
        help="Optional subset of repos to remove (repeatable). Omit to remove all.",
    ),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove worktrees for a feature workspace (all repos if none specified)."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")

    targets: List[str] = repos
    if not targets:
        targets = sorted([p.name for p in feature_dir.iterdir() if p.is_dir() and is_git_repo(p)])

    if not targets:
        console.print("[yellow]Nothing to remove.[/yellow]")
        return

    table = Table(title=f"Remove worktrees: {feature}", header_style="bold")
    table.add_column("Repo")
    table.add_column("Path")
    for r in targets:
        table.add_row(r, str(_worktree_path(cfg, feature, r)))
    console.print(table)

    if not yes:
        if not typer.confirm("Proceed?"):
            raise typer.Exit(code=1)

    for r in targets:
        repo_path = _require_root_repo(cfg, r)
        wt_path = _worktree_path(cfg, feature, r)
        if wt_path.exists():
            console.print(f"[red]-[/red] worktree remove {feature}/{r} → {wt_path}")
            worktree_remove(repo_path, wt_path, force=True)

    # Refresh/remove workspace file based on what's left.
    remaining_repos = [p for p in feature_dir.iterdir() if p.is_dir() and is_git_repo(p)]
    if remaining_repos:
        _refresh_workspace(cfg, feature)
    else:
        ws_path = feature_dir / f"{feature}.code-workspace"
        try:
            if ws_path.exists():
                ws_path.unlink()
        except Exception:
            pass
        try:
            feature_dir.rmdir()
        except Exception:
            pass

    console.print("[green]✓[/green] removed")


config_app = typer.Typer(help="Manage pal configuration files.")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing .pal.toml"),
):
    """Create a local .pal.toml template in the projects root."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    path = cfg.local_config_path
    if path.exists() and not force:
        raise typer.BadParameter(f"{path} already exists (use --force to overwrite).")
    path.write_text(
        "# pal local configuration (TOML)\n"
        'root = "."\n'
        'worktree_root = "_wt"\n'
        'branch_prefix = "feat"\n'
        '# repos = ["repo1", "repo2"]\n'
        '# editor = "cursor"\n'
        "\n"
        "[codex]\n"
        'sandbox = "workspace-write"\n'
        'approval = "on-request"\n'
        "full_auto = false\n"
        "\n"
        "[local_files]\n"
        "enabled = false\n"
        "overwrite = false\n"
        "# Paths are explicit repo-relative files:\n"
        '# paths = ["backend/.env.prod.local"]\n'
        "# Patterns are repo-relative globs (supports **):\n"
        '# patterns = ["**/.env*", "**/.npmrc", "**/.envrc"]\n'
        "\n"
        "# Per-repo overrides:\n"
        "# [local_files.repos.integrations]\n"
        '# paths = ["apps/searcher/collections/.env"]\n'
        '# patterns = ["apps/**/.npmrc"]\n',
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] wrote {path}")


@config_app.command("show")
def config_show(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Show resolved configuration."""
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    console.print(Panel.fit(
        f"root: {cfg.root}\n"
        f"worktree_root: {cfg.worktree_root}\n"
        f"branch_prefix: {cfg.branch_prefix}\n"
        f"repos allowlist: {cfg.repos if cfg.repos else '(auto)'}\n"
        f"editor: {cfg.editor or '(auto)'}\n"
        f"codex: sandbox={cfg.codex.sandbox} approval={cfg.codex.approval} full_auto={cfg.codex.full_auto}\n"
        f"global config: {global_config_path()}\n"
        f"local config: {cfg.local_config_path}",
        title="pal config",
    ))
