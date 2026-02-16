from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .config import load_config, global_config_path
from .completion import (
    complete_agent,
    complete_feature,
    complete_repo,
    complete_repo_in_feature,
)
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
from .claude import run_interactive as run_claude_interactive
from .codex import run_interactive as run_codex_interactive
from .local_files import copy_local_files, resolve_local_file_paths

app = typer.Typer(
    add_completion=True,
    help="Pal: multi-repo feature workspaces using git worktrees (agent-friendly).",
    no_args_is_help=True,
)
console = Console()
AGENTS = ("claude", "codex")
CODEX_BLOCKED_PLAN_ARGS = {
    "app-server",
    "cloud",
    "completion",
    "debug",
    "exec",
    "features",
    "fork",
    "help",
    "login",
    "logout",
    "mcp",
    "proto",
    "resume",
    "r",
    "e",
    "sandbox",
}


def _pal_version() -> str:
    try:
        from importlib.metadata import version  # py>=3.8

        return version("pal")
    except Exception:
        return "0.0.0"


def _print_version(value: bool) -> None:
    if not value:
        return
    console.print(f"pal {_pal_version()}")
    raise typer.Exit(code=0)


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
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_print_version,
        is_eager=True,
    ),
):
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


def _effective_codex_config(cfg):
    codex_cfg = deepcopy(cfg.codex)
    if cfg.agent.add_dirs:
        merged = [*cfg.agent.add_dirs, *codex_cfg.add_dirs]
        codex_cfg.add_dirs = list(dict.fromkeys(merged))
    return codex_cfg


def _resolve_agent(agent: str) -> str:
    normalized = agent.strip().lower()
    if normalized not in AGENTS:
        raise typer.BadParameter(f"Unknown agent '{agent}'. Supported agents: {', '.join(AGENTS)}")
    return normalized


def _has_flag(args: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in args)


def _flag_value(args: list[str], name: str) -> Optional[str]:
    for idx, arg in enumerate(args):
        if arg == name:
            return args[idx + 1] if idx + 1 < len(args) else None
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return None


def _remove_flag(args: list[str], name: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == name:
            i += 2
            continue
        if arg.startswith(f"{name}="):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def _effective_claude_add_dirs(cfg) -> list[str]:
    merged = [*cfg.agent.add_dirs, *cfg.claude.add_dirs]
    return list(dict.fromkeys(merged))


def _default_claude_mode_for_intent(cfg, intent: str) -> str:
    if intent == "plan":
        return "plan"
    return cfg.claude.permission_mode.strip()


def _is_bypass_permission_mode(mode: str) -> bool:
    normalized = mode.strip().lower()
    return normalized in {"bypasspermissions", "bypass_permissions", "bypass-permissions"}


def _validate_claude_permissions(cfg, args: list[str]) -> None:
    if cfg.claude.allow_bypass_permissions:
        return
    if _has_flag(args, "--dangerously-skip-permissions"):
        raise typer.BadParameter(
            "Bypass permissions are disabled by config ([claude].allow_bypass_permissions=false)."
        )
    mode = _flag_value(args, "--permission-mode")
    if mode and _is_bypass_permission_mode(mode):
        raise typer.BadParameter(
            "Bypass permissions mode is disabled by config "
            "([claude].allow_bypass_permissions=false)."
        )


def _effective_claude_args(cfg, intent: str, raw_args: list[str]) -> list[str]:
    args = list(raw_args)
    if not _has_flag(args, "--permission-mode"):
        mode = _default_claude_mode_for_intent(cfg, intent)
        if mode:
            args = ["--permission-mode", mode, *args]
    if cfg.claude.model and not _has_flag(args, "--model"):
        args = ["--model", cfg.claude.model, *args]
    if cfg.claude.extra_args:
        args = [*cfg.claude.extra_args, *args]
    _validate_claude_permissions(cfg, args)
    return args


def _codex_plan_args(args: list[str]) -> list[str]:
    if not args:
        return ["/plan"]
    if any(token.startswith("-") for token in args):
        raise typer.BadParameter(
            "pal plan <feature> codex accepts only a planning prompt. "
            "Use `pal run <feature> codex ...` for raw Codex flags."
        )
    if args[0].lower() in CODEX_BLOCKED_PLAN_ARGS:
        raise typer.BadParameter(
            "pal plan <feature> codex is interactive planning only. "
            "Use `pal run <feature> codex ...` for Codex subcommands."
        )
    return [f"/plan {' '.join(args)}"]


def _run_agent(feature_dir: Path, cfg, agent: str, intent: str, raw_args: list[str]) -> None:
    if agent == "codex":
        codex_cfg = _effective_codex_config(cfg)
        codex_args = raw_args
        if intent == "plan":
            codex_args = _codex_plan_args(raw_args)
        console.print(
            Panel.fit(
                f"workspace: {feature_dir}\n"
                f"agent: codex\n"
                f"intent: {intent}\n"
                f"sandbox={codex_cfg.sandbox} approval={codex_cfg.approval} full_auto={codex_cfg.full_auto}",
                title="Launching Agent",
            )
        )
        run_codex_interactive(feature_dir, codex_cfg, extra_args=codex_args or None)
        return

    claude_args = _effective_claude_args(cfg, intent, raw_args)
    claude_add_dirs = _effective_claude_add_dirs(cfg)
    claude_mode = _flag_value(claude_args, "--permission-mode") or "(default)"
    claude_model = _flag_value(claude_args, "--model") or "(default)"

    console.print(
        Panel.fit(
            f"workspace: {feature_dir}\n"
            f"agent: claude\n"
            f"intent: {intent}\n"
            f"permission_mode={claude_mode}\n"
            f"model={claude_model}\n"
            f"add_dirs={claude_add_dirs}",
            title="Launching Agent",
        )
    )
    run_claude_interactive(
        feature_dir,
        add_dirs=claude_add_dirs,
        extra_args=claude_args or None,
    )


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
        if exists_on_path(exe):
            console.print(f"[green]✓[/green] {label}: {exe}")
            return True
        else:
            console.print(f"[red]✗[/red] {label}: {exe} (not found in PATH)")
            return False

    if not chk("git", "Git"):
        ok = False
    has_codex = chk("codex", "Codex CLI")
    has_claude = chk("claude", "Claude Code CLI")
    if not has_codex and not has_claude:
        ok = False
        console.print("[red]At least one agent CLI is required: codex or claude.[/red]")
    if exists_on_path("cursor") or exists_on_path("code"):
        chk("cursor" if exists_on_path("cursor") else "code", "Editor (auto-detected)")
    else:
        console.print(
            "[yellow]•[/yellow] Editor: cursor/code not found (pal open will still write .code-workspace)"
        )

    console.print()
    console.print(
        Panel.fit(
            f"[bold]Resolved config[/bold]\n"
            f"root: {cfg.root}\n"
            f"worktree_root: {cfg.worktree_root}\n"
            f"branch_prefix: {cfg.branch_prefix}\n"
            f"global config: {global_config_path()}\n"
            f"local config: {cfg.local_config_path}",
            title="pal",
        )
    )

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
        console.print(
            "[yellow]No repos found.[/yellow] Put repos under root or set repos=[...] in .pal.toml."
        )
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
        console.print(
            "[yellow]No feature workspaces yet.[/yellow] Try: pal new <feature> <repo...>"
        )
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
    console.print(
        f"[cyan]+[/cyan] worktree add {feature}/{repo} → {wt_path} (branch {b}{' [new]' if create else ''})"
    )
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
        console.print(
            f"[yellow]•[/yellow] skipped existing in worktree for {repo}: {len(result.skipped_existing)}"
        )
    invalid_count = len(result.skipped_invalid) + len(skipped_invalid)
    if invalid_count:
        console.print(f"[yellow]•[/yellow] skipped invalid specs for {repo}: {invalid_count}")


@app.command()
def new(
    feature: str = typer.Argument(..., help="Feature workspace name (folder under worktree_root)."),
    repos: List[str] = typer.Argument(
        ..., help="One or more repo folder names under root.", autocompletion=complete_repo
    ),
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
    feature: str = typer.Argument(
        ..., help="Existing feature workspace name.", autocompletion=complete_feature
    ),
    repos: List[str] = typer.Argument(
        ..., help="One or more repos to add to the feature workspace.", autocompletion=complete_repo
    ),
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
    console.print(
        "[yellow]Tip:[/yellow] Restart any interactive agent session if it's already running."
    )


@app.command()
def status(
    feature: str = typer.Argument(
        ..., help="Feature workspace name.", autocompletion=complete_feature
    ),
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
    feature: str = typer.Argument(..., autocompletion=complete_feature),
    editor: str = typer.Option(
        "", "--editor", help="Force editor command (cursor|code). Auto-detect if empty."
    ),
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
        console.print(
            "[yellow]Editor not found.[/yellow] Install Cursor ('cursor') or VS Code ('code') CLI to auto-open."
        )
        return

    console.print(f"[cyan]→[/cyan] opening in {ed}: {ws_path}")
    import subprocess

    subprocess.Popen([ed, str(ws_path)])  # intentionally not check=True (editor exits immediately)
    console.print("[green]✓[/green] opened")


def _agent_entrypoint(
    ctx: typer.Context,
    *,
    feature: str,
    agent: str,
    root: Path,
    worktree_root: Optional[Path],
    branch_prefix: Optional[str],
    intent: str,
):
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    feature_dir = _feature_dir(cfg, feature)
    if not feature_dir.exists():
        raise typer.BadParameter(f"Feature workspace '{feature}' not found at {feature_dir}")
    resolved_agent = _resolve_agent(agent)
    _run_agent(feature_dir, cfg, resolved_agent, intent, list(ctx.args))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    feature: str = typer.Argument(..., autocompletion=complete_feature),
    agent: str = typer.Argument(..., autocompletion=complete_agent),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Run an agent CLI in a feature workspace."""
    _agent_entrypoint(
        ctx,
        feature=feature,
        agent=agent,
        root=root,
        worktree_root=worktree_root,
        branch_prefix=branch_prefix,
        intent="run",
    )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def plan(
    ctx: typer.Context,
    feature: str = typer.Argument(..., autocompletion=complete_feature),
    agent: str = typer.Argument(..., autocompletion=complete_agent),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Run an agent in planning mode."""
    _agent_entrypoint(
        ctx,
        feature=feature,
        agent=agent,
        root=root,
        worktree_root=worktree_root,
        branch_prefix=branch_prefix,
        intent="plan",
    )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def implement(
    ctx: typer.Context,
    feature: str = typer.Argument(..., autocompletion=complete_feature),
    agent: str = typer.Argument(..., autocompletion=complete_agent),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
):
    """Run an agent in implementation mode."""
    _agent_entrypoint(
        ctx,
        feature=feature,
        agent=agent,
        root=root,
        worktree_root=worktree_root,
        branch_prefix=branch_prefix,
        intent="implement",
    )


@app.command()
def rm(
    feature: str = typer.Argument(..., autocompletion=complete_feature),
    repos: List[str] = typer.Option(
        [],
        "--repo",
        help="Optional subset of repos to remove (repeatable). Omit to remove all.",
        autocompletion=complete_repo_in_feature,
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
        # Allow removing empty/broken feature workspaces too (e.g. no git repos left, or stray dirs only).
        if not yes:
            if not typer.confirm(
                f"No git worktrees found in '{feature}'. Remove the feature folder anyway?"
            ):
                raise typer.Exit(code=1)
        import shutil

        shutil.rmtree(feature_dir, ignore_errors=True)
        console.print("[green]✓[/green] removed")
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
        import shutil

        shutil.rmtree(feature_dir, ignore_errors=True)

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
        "[claude]\n"
        'permission_mode = "acceptEdits"\n'
        '# model = "sonnet"\n'
        "# add_dirs = []\n"
        "# extra_args = []\n"
        "allow_bypass_permissions = false\n"
        "\n"
        "[agent]\n"
        "# Shared writable roots for all agent CLIs (Codex, Claude Code).\n"
        '# add_dirs = ["~/.npm", "~/.cache/prisma"]\n'
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
    console.print(
        Panel.fit(
            f"root: {cfg.root}\n"
            f"worktree_root: {cfg.worktree_root}\n"
            f"branch_prefix: {cfg.branch_prefix}\n"
            f"repos allowlist: {cfg.repos if cfg.repos else '(auto)'}\n"
            f"editor: {cfg.editor or '(auto)'}\n"
            f"agent: add_dirs={cfg.agent.add_dirs}\n"
            f"codex: sandbox={cfg.codex.sandbox} approval={cfg.codex.approval} full_auto={cfg.codex.full_auto}\n"
            f"codex: add_dirs={cfg.codex.add_dirs}\n"
            f"claude: permission_mode={cfg.claude.permission_mode}\n"
            f"claude: model={cfg.claude.model or '(default)'} allow_bypass_permissions={cfg.claude.allow_bypass_permissions}\n"
            f"claude: add_dirs={cfg.claude.add_dirs}\n"
            f"claude: extra_args={cfg.claude.extra_args}\n"
            "agents supported: codex, claude\n"
            f"global config: {global_config_path()}\n"
            f"local config: {cfg.local_config_path}",
            title="pal config",
        )
    )
