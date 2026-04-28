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
from .workflows.templates import (
    WorkflowTemplateError,
    list_workflow_templates,
    write_workflow_template,
)


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


def _write_workflow_template_or_error(
    root: Path,
    *,
    workflow_name: str,
    template: str,
    provider: str,
    work_type: str,
    repos: list[str],
    force: bool,
) -> Path:
    try:
        return write_workflow_template(
            root,
            workflow_name=workflow_name,
            template=template,
            provider=provider,
            work_type=work_type,
            repos=repos,
            force=force,
        )
    except WorkflowTemplateError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _print_workflow_template_table() -> None:
    table = Table(title="Flow workflow templates", header_style="bold")
    table.add_column("Template")
    table.add_column("Mode")
    table.add_column("Description")
    for template in list_workflow_templates():
        table.add_row(template.name, template.mode, template.description)
    console.print(table)


@flow_app.command("init")
def flow_init(
    workflow: str = typer.Argument(
        "dev-complex",
        help="Workflow name to create under .pal/flows.",
    ),
    template: str = typer.Option(
        "dev-complex",
        "--template",
        "-t",
        help="Built-in workflow template.",
    ),
    provider: str = typer.Option(
        "codex",
        "--provider",
        help="Default provider in the generated workflow spec.",
    ),
    work_type: str = typer.Option("dev", "--work-type", help="Workflow work_type value."),
    repos: Optional[List[str]] = typer.Option(
        None,
        "--repo",
        "-R",
        help="Repo included in the generated workflow. Repeatable.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing workflow spec."),
    list_templates: bool = typer.Option(
        False,
        "--list-templates",
        help="List built-in workflow templates and exit.",
    ),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Create a checked-in workflow spec from a built-in template."""
    if list_templates:
        _print_workflow_template_table()
        return

    service = _service_from_options(root, worktree_root, branch_prefix)
    _provider_or_error(service, provider)
    path = _write_workflow_template_or_error(
        service.workflow_library.root,
        workflow_name=workflow,
        template=template,
        provider=provider,
        work_type=work_type,
        repos=list(repos or []),
        force=force,
    )
    result = service.validate_workflow(path)
    if not result.valid:
        raise typer.BadParameter("Generated workflow spec is invalid: " + "; ".join(result.errors))
    console.print(
        Panel.fit(
            f"path: {path}\n"
            f"template: {template}\n"
            f"provider: {provider}\n"
            f"work_type: {work_type}\n"
            f"repos: {', '.join(repos or []) if repos else '(none)'}",
            title="pal flow initialized",
        )
    )


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
    workspace: str = typer.Option(
        "state-only",
        "--workspace",
        help="Workspace preparation mode: state-only, create, reuse, or validate.",
    ),
    copy_local: Optional[bool] = typer.Option(
        None,
        "--copy-local/--no-copy-local",
        help="Copy local files into prepared worktrees (default: config).",
    ),
    overwrite_local: Optional[bool] = typer.Option(
        None,
        "--overwrite-local/--no-overwrite-local",
        help="Overwrite existing local files when copying (default: config).",
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
        workspace_mode=workspace,
        copy_local=copy_local,
        overwrite_local=overwrite_local,
    )
    console.print(
        Panel.fit(
            f"run_id: {run.run_id}\n"
            f"feature: {run.feature}\n"
            f"phase: {run.current_phase.value}\n"
            f"status: {run.status.value}\n"
            f"workspace: {workspace}",
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


@flow_app.command("execute")
def flow_execute(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider execution filter."),
    agent: Optional[str] = typer.Option(None, "--agent", help="Single rendered agent ID to run."),
    force_policy: bool = typer.Option(False, "--force-policy", help="Override observer policy."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Execute the current phase brief through local headless providers."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    summary = _change_or_error(
        service,
        "execute_phase",
        feature,
        run_id=run_id,
        provider_name=provider or "",
        agent_id=agent or "",
        force_policy=force_policy,
    )
    manifests = (
        "\n".join(execution.paths["manifest"] for execution in summary.executions) or "(none)"
    )
    console.print(
        Panel.fit(
            f"run_id: {summary.run.run_id}\n"
            f"phase: {summary.phase.value}\n"
            f"status: {summary.status}\n"
            f"executions: {len(summary.executions)}\n"
            f"manifests:\n{manifests}",
            title="pal flow executed",
        )
    )


@flow_app.command("artifacts")
def flow_artifacts(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Validate required artifacts for the current phase."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    validation = _change_or_error(service, "check_artifacts", feature, run_id=run_id)
    table = Table(title=f"Flow artifacts: {feature}", header_style="bold")
    table.add_column("Artifact")
    table.add_column("Exists")
    table.add_column("Path")
    for check in validation.checks:
        table.add_row(check.name, "yes" if check.exists else "no", check.path)
    if not validation.checks:
        table.add_row("(none)", "yes", "(no required artifacts)")
    console.print(table)
    if not validation.valid:
        raise typer.Exit(1)


@flow_app.command("run")
def flow_run(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider execution filter."),
    max_phases: int = typer.Option(10, "--max-phases", help="Maximum phases to execute."),
    co_driver_auto_advance: bool = typer.Option(
        False,
        "--co-driver-auto-advance",
        help="Allow co-driver phases to advance automatically.",
    ),
    force_policy: bool = typer.Option(False, "--force-policy", help="Override observer policy."),
    force_artifacts: bool = typer.Option(
        False,
        "--force-artifacts",
        help="Allow auto-advance with missing required artifacts.",
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Execute policy-aware phases until the flow stops."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    summary = _change_or_error(
        service,
        "run_flow",
        feature,
        run_id=run_id,
        provider_name=provider or "",
        max_phases=max_phases,
        co_driver_auto_advance=co_driver_auto_advance,
        force_policy=force_policy,
        force_artifacts=force_artifacts,
    )
    lines = [
        f"run_id: {summary.run.run_id}",
        f"status: {summary.status}",
        f"phase: {summary.run.current_phase.value}",
        f"run_status: {summary.run.status.value}",
        f"steps: {len(summary.steps)}",
    ]
    console.print(Panel.fit("\n".join(lines), title="pal flow run"))


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
    force_artifacts: bool = typer.Option(
        False,
        "--force-artifacts",
        help="Advance even if required artifacts are missing.",
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Advance a run through the workflow state machine."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _change_or_error(
        service,
        "advance",
        feature,
        run_id=run_id,
        signal=signal,
        force_artifacts=force_artifacts,
    )
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
