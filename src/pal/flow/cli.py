from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..cli_config import cfg_from_options
from .models import FlowPhase, FlowRun
from .runtime import build_local_flow_service
from .workflows.library import WorkflowSpecError


flow_app = typer.Typer(help="Manage local agentic workflow runs.", no_args_is_help=True)
console = Console()


def _cfg_from_ctx(
    root: Path,
    worktree_root: Optional[Path],
    branch_prefix: Optional[str],
):
    return cfg_from_options(root, worktree_root, branch_prefix)


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


def _provider_or_error(service, provider: str):  # noqa: ANN001
    try:
        return service.provider(provider)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _start_or_error(service, **kwargs):  # noqa: ANN001, ANN003
    try:
        return service.start(**kwargs)
    except (ValueError, WorkflowSpecError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _change_or_error(service, method: str, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    try:
        return getattr(service, method)(*args, **kwargs)
    except (FileNotFoundError, ValueError, WorkflowSpecError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@flow_app.command("start")
def flow_start(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    repos: Optional[List[str]] = typer.Option(
        None,
        "--repo",
        "-R",
        help="Repo included in this run. Repeatable.",
    ),
    mode: Optional[str] = typer.Option(None, "--mode", help="Run mode: routine or complex."),
    phase: Optional[str] = typer.Option(None, "--phase", help="Initial phase."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider override."),
    workflow: Optional[str] = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow spec name or path under .pal/flows.",
    ),
    headless: bool = typer.Option(False, "--headless", help="Run provider in local headless mode."),
    prompt: str = typer.Option("", "--prompt", help="Prompt for explicit headless provider runs."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Start a local flow run with durable state and events."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    if provider:
        _provider_or_error(service, provider)
    if headless and not prompt.strip():
        raise typer.BadParameter("--prompt is required when --headless is set.")
    run = _start_or_error(
        service,
        feature=feature,
        repos=list(repos or []),
        mode=mode,
        phase=_parse_phase(phase) if phase else None,
        provider_name=provider,
        headless=headless,
        prompt=prompt,
        workflow=workflow,
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
    if run.workflow_name:
        table.add_row("workflow", run.workflow_name)
        table.add_row("work_type", run.work_type)
    if run.blocked_reason:
        table.add_row("blocked_reason", run.blocked_reason)
    if run.approvals:
        table.add_row("approvals", ", ".join(sorted(run.approvals)))
    table.add_row("phase_history", str(len(run.phase_history)))
    table.add_row("repos", ", ".join(run.repos) if run.repos else "(none)")
    table.add_row("artifact_root", run.artifact_root)
    console.print(table)


@flow_app.command("providers")
def flow_providers(
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Show provider install/auth/capability preflight without launching model work."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    table = Table(title="Flow providers", header_style="bold")
    table.add_column("Provider")
    table.add_column("Installed")
    table.add_column("Auth")
    table.add_column("Version")
    table.add_column("Modes")
    for preflight in service.preflight_all():
        table.add_row(
            preflight.provider,
            "yes" if preflight.installed else "no",
            preflight.auth.status,
            preflight.version or "(unknown)",
            ", ".join(preflight.capabilities.execution_modes),
        )
    console.print(table)


@flow_app.command("validate")
def flow_validate(
    workflow: Optional[str] = typer.Argument(
        None, help="Workflow spec name/path. Defaults to all."
    ),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Validate checked-in workflow specs without launching agents."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    results = [service.validate_workflow(workflow)] if workflow else service.validate_workflows()
    if not results:
        console.print("[yellow]No workflow specs found.[/yellow]")
        return

    table = Table(title="Flow workflow validation", header_style="bold")
    table.add_column("Workflow")
    table.add_column("Work Type")
    table.add_column("Path")
    table.add_column("Status")
    table.add_column("Errors")
    for result in results:
        table.add_row(
            result.name or "(unknown)",
            result.work_type or "(unknown)",
            str(result.path),
            "valid" if result.valid else "invalid",
            "; ".join(result.errors),
        )
    console.print(table)
    if any(not result.valid for result in results):
        raise typer.Exit(1)


@flow_app.command("render")
def flow_render(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider guidance filter."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Render the current phase brief without launching agents."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    brief = _change_or_error(
        service,
        "render_phase",
        feature,
        run_id=run_id,
        provider_name=provider or "",
    )
    console.print(
        Panel.fit(
            f"run_id: {brief.run.run_id}\n"
            f"phase: {brief.phase.value}\n"
            f"markdown: {brief.paths['markdown']}\n"
            f"json: {brief.paths['json']}",
            title="pal flow rendered",
        )
    )


@flow_app.command("approve")
def flow_approve(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    phase: Optional[str] = typer.Option(None, "--phase", help="Phase to approve."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Approve a gated phase so it can advance."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _change_or_error(
        service,
        "approve",
        feature,
        run_id=run_id,
        phase=_parse_phase(phase) if phase else None,
    )
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\nphase: {(phase or run.current_phase.value)}\nstatus: {run.status.value}",
            title="pal flow approved",
        )
    )


@flow_app.command("advance")
def flow_advance(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    signal: str = typer.Option("complete", "--on", help="Transition signal."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Advance a run through the workflow state machine."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _change_or_error(service, "advance", feature, run_id=run_id, signal=signal)
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\nphase: {run.current_phase.value}\nstatus: {run.status.value}",
            title="pal flow advanced",
        )
    )


@flow_app.command("block")
def flow_block(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    reason: str = typer.Option(..., "--reason", "-m", help="Why the run is blocked."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Mark the current phase as blocked."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _change_or_error(service, "block", feature, run_id=run_id, reason=reason)
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\nphase: {run.current_phase.value}\nstatus: {run.status.value}",
            title="pal flow blocked",
        )
    )


@flow_app.command("replan")
def flow_replan(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    phase: Optional[str] = typer.Option(None, "--phase", help="Phase to re-enter."),
    reason: str = typer.Option("", "--reason", "-m", help="Why replanning is needed."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Clear a block and re-enter a planning phase."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _change_or_error(
        service,
        "replan",
        feature,
        run_id=run_id,
        phase=_parse_phase(phase) if phase else None,
        reason=reason,
    )
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\nphase: {run.current_phase.value}\nstatus: {run.status.value}",
            title="pal flow replanned",
        )
    )


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
