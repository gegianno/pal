from __future__ import annotations

import json
from pathlib import Path
import time
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


def _load_workflow_or_error(service, workflow: str):  # noqa: ANN001
    try:
        return service.load_workflow(workflow)
    except WorkflowSpecError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _print_workflow_inspection(spec) -> None:  # noqa: ANN001
    console.print(
        Panel.fit(
            f"name: {spec.name}\n"
            f"work_type: {spec.work_type}\n"
            f"mode: {spec.mode}\n"
            f"path: {spec.path}\n"
            f"default_provider: {spec.defaults.provider or '(service default)'}\n"
            f"repos: {', '.join(spec.repos) if spec.repos else '(none)'}",
            title="pal flow workflow",
        )
    )
    table = Table(title=f"Workflow phases: {spec.name}", header_style="bold")
    table.add_column("#")
    table.add_column("Phase")
    table.add_column("Policy")
    table.add_column("Provider")
    table.add_column("Approval")
    table.add_column("Agents")
    table.add_column("Artifacts")
    table.add_column("Transitions")
    for index, phase in enumerate(spec.phases, start=1):
        table.add_row(
            str(index),
            phase.id.value,
            phase.policy.value,
            phase.provider or spec.defaults.provider or "(default)",
            "yes" if phase.requires_approval else "no",
            ", ".join(f"{agent.id}({agent.provider})" for agent in phase.agents) or "(none)",
            ", ".join(phase.required_artifacts) or "(none)",
            ", ".join(f"{transition.on}->{transition.to.value}" for transition in phase.transitions)
            or "(implicit complete)",
        )
    console.print(table)


def _read_body_file(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"Cannot read body file '{path}': {exc}") from exc


def _print_ship_summary(summary) -> None:  # noqa: ANN001
    table = Table(title=f"Flow ship: {summary.feature}", header_style="bold")
    table.add_column("Repo")
    table.add_column("Changed")
    table.add_column("Commit")
    table.add_column("Push")
    table.add_column("PR")
    table.add_column("URL")
    table.add_column("Error")
    for repo in summary.repos:
        table.add_row(
            repo.repo,
            "yes" if repo.changed else "no",
            repo.commit_status,
            repo.push_status,
            repo.pr_status,
            repo.pr_url,
            repo.error,
        )
    console.print(table)
    console.print(f"manifest: {summary.paths.get('manifest', '')}")


def _print_events_table(feature: str, events: list) -> None:  # noqa: ANN001
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


def _follow_should_continue(max_polls: int | None, polls: int) -> bool:
    return max_polls is None or polls < max_polls


def _follow_events(
    service,  # noqa: ANN001
    feature: str,
    run_id: str | None,
    *,
    seen_ids: set[str],
    poll_interval: float,
    max_polls: int | None,
) -> None:
    polls = 0
    while _follow_should_continue(max_polls, polls):
        time.sleep(poll_interval)
        polls += 1
        events = service.events(feature, run_id)
        new_events = [event for event in events if event.id not in seen_ids]
        if new_events:
            _print_events_table(feature, new_events)
            seen_ids.update(event.id for event in new_events)


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


@flow_app.command("inspect")
def flow_inspect(
    workflow: str = typer.Argument(..., help="Workflow spec name/path."),
    json_output: bool = typer.Option(False, "--json", help="Print workflow as JSON."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Inspect phases, agents, policies, artifacts, and transitions in a workflow spec."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    spec = _load_workflow_or_error(service, workflow)
    if json_output:
        console.print(json.dumps(spec.to_run_dict(), indent=2, sort_keys=True))
        return
    _print_workflow_inspection(spec)


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


@flow_app.command("ship")
def flow_ship(
    feature: str = typer.Argument(..., help="Feature workspace name."),
    repos: Optional[List[str]] = typer.Option(
        None,
        "--repo",
        "-R",
        help="Repo to include. Defaults to run repos or discovered feature repos.",
    ),
    base: str = typer.Option("main", "--base", help="PR base branch."),
    title: str = typer.Option("", "--title", help="PR title. Defaults to feature/workflow."),
    body: str = typer.Option("", "--body", help="PR body markdown."),
    body_file: Optional[Path] = typer.Option(None, "--body-file", help="Read PR body markdown."),
    commit: bool = typer.Option(False, "--commit", help="Commit all changes in selected repos."),
    message: str = typer.Option("", "--message", "-m", help="Commit message for --commit."),
    push: bool = typer.Option(False, "--push", help="Push selected repo branches."),
    create_pr: bool = typer.Option(False, "--create-pr", help="Create or reuse GitHub PRs via gh."),
    draft: bool = typer.Option(False, "--draft", help="Create draft PRs when --create-pr is set."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview commit/push/PR actions without mutating repos.",
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Prepare shipping artifacts and optionally commit, push, and create PRs."""
    service = _service_from_options(root, worktree_root, branch_prefix)
    summary = _change_or_error(
        service,
        "ship",
        feature,
        run_id=run_id,
        repos=list(repos or []),
        base=base,
        title=title,
        body=_read_body_file(body_file) or body,
        dry_run=dry_run,
        commit=commit,
        commit_message=message,
        push=push,
        create_pr=create_pr,
        draft=draft,
    )
    _print_ship_summary(summary)
    if summary.status != "completed":
        raise typer.Exit(1)


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
    follow: bool = typer.Option(False, "--follow", "-f", help="Poll and print new events."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Seconds between polls."),
    max_polls: Optional[int] = typer.Option(
        None,
        "--max-polls",
        help="Testing guard for --follow.",
        hidden=True,
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID. Defaults to latest."),
    root: Path = typer.Option(Path("."), "--root", "-r"),
    worktree_root: Optional[Path] = typer.Option(None, "--worktree-root"),
    branch_prefix: Optional[str] = typer.Option(None, "--branch-prefix"),
) -> None:
    """Print the recorded event stream for a local flow run."""
    if poll_interval < 0:
        raise typer.BadParameter("--poll-interval must be non-negative.")
    if max_polls is not None and max_polls < 0:
        raise typer.BadParameter("--max-polls must be non-negative.")
    service = _service_from_options(root, worktree_root, branch_prefix)
    run = _load_run_or_error(service, feature, run_id)
    resolved_run_id = run.run_id
    events = service.events(feature, resolved_run_id)
    if not events:
        console.print("[yellow]No events recorded.[/yellow]")
    else:
        _print_events_table(feature, events)
    if follow:
        _follow_events(
            service,
            feature,
            resolved_run_id,
            seen_ids={event.id for event in events},
            poll_interval=poll_interval,
            max_polls=max_polls,
        )
