from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .flow_models import FlowPhase, FlowRun
from .flow_runtime import build_local_flow_service


flow_app = typer.Typer(help="Manage local agentic workflow runs.", no_args_is_help=True)
console = Console()


def _cfg_from_ctx(
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


def _parse_phase(value: str) -> FlowPhase:
    try:
        return FlowPhase(value)
    except ValueError as exc:
        valid = ", ".join(phase.value for phase in FlowPhase)
        raise typer.BadParameter(f"Unknown phase '{value}'. Expected one of: {valid}") from exc


def _load_run_or_error(service, feature: str, run_id: str | None) -> FlowRun:  # noqa: ANN001
    try:
        return service.status(feature, run_id)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _service_from_options(root: Path, worktree_root: Optional[Path], branch_prefix: Optional[str]):
    cfg = _cfg_from_ctx(root, worktree_root, branch_prefix)
    return build_local_flow_service(cfg)


@flow_app.command("start")
def flow_start(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    repos: Optional[List[str]] = typer.Option(
        None,
        "--repo",
        "-R",
        help="Repo included in this run. Repeatable.",
    ),
    mode: str = typer.Option("routine", "--mode", help="Run mode: routine or complex."),
    phase: str = typer.Option("explore", "--phase", help="Initial phase."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Start a local flow run with durable state and events."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = service.start(
        feature=feature,
        repos=list(repos or []),
        mode=mode,
        phase=_parse_phase(phase),
    )
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\n"
            f"feature: {run.feature}\n"
            f"phase: {run.current_phase.value}\n"
            f"status: {run.status.value}",
            title="pal flow started",
        )
    )


@flow_app.command("status")
def flow_status(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Show local flow run status."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _load_run_or_error(service, feature, run_id)
    table = Table(title=f"Flow status: {feature}", header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("run_id", run.run_id)
    table.add_row("mode", run.mode)
    table.add_row("phase", run.current_phase.value)
    table.add_row("status", run.status.value)
    table.add_row("repos", ", ".join(run.repos) if run.repos else "(none)")
    table.add_row("artifact_root", run.artifact_root)
    console.print(table)


@flow_app.command("watch")
def flow_watch(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Print the recorded event stream for a local flow run."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    _load_run_or_error(service, feature, run_id)
    events = service.events(feature, run_id)
    if not events:
        console.print("[yellow]No events recorded.[/yellow]")
        return
    table = Table(title=f"Flow events: {feature}", header_style="bold")
    table.add_column("Time")
    table.add_column("Type")
    table.add_column("Actor")
    table.add_column("Phase")
    table.add_column("Summary")
    for event in events:
        table.add_row(
            event.timestamp,
            event.type,
            event.actor,
            event.phase.value if event.phase else "",
            str(event.payload.get("summary", "")),
        )
    console.print(table)
