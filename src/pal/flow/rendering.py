from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Any

from .artifacts import resolve_artifact_path
from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun
from .workflows.models import WorkflowAgent, WorkflowPhase, WorkflowSpec, WorkflowTools


@dataclass(frozen=True)
class ResolvedPhaseAgent:
    id: str
    role: str
    provider: str
    prompt: str
    produces: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    tools: WorkflowTools = field(default_factory=WorkflowTools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "provider": self.provider,
            "prompt": self.prompt,
            "produces": list(self.produces),
            "requires": list(self.requires),
            "tools": self.tools.to_dict(),
        }


@dataclass(frozen=True)
class PhaseBrief:
    run: FlowRun
    phase: FlowPhase
    policy: FlowPolicy
    workflow: WorkflowSpec | None
    agents: list[ResolvedPhaseAgent]
    required_artifacts: list[str]
    events: list[FlowEvent]
    provider_guidance: dict[str, str]
    rendered_at: str
    pal_command: str = "pal"
    paths: dict[str, str] = field(default_factory=dict)

    def with_paths(self, paths: dict[str, str]) -> PhaseBrief:
        return PhaseBrief(
            run=self.run,
            phase=self.phase,
            policy=self.policy,
            workflow=self.workflow,
            agents=self.agents,
            required_artifacts=self.required_artifacts,
            events=self.events,
            provider_guidance=self.provider_guidance,
            rendered_at=self.rendered_at,
            pal_command=self.pal_command,
            paths=dict(paths),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rendered_at": self.rendered_at,
            "pal_command": self.pal_command,
            "run": {
                "run_id": self.run.run_id,
                "feature": self.run.feature,
                "mode": self.run.mode,
                "repos": list(self.run.repos),
                "status": self.run.status.value,
                "artifact_root": self.run.artifact_root,
                "workflow_name": self.run.workflow_name,
                "work_type": self.run.work_type,
                "request": self.run.request,
            },
            "phase": {
                "id": self.phase.value,
                "policy": self.policy.value,
                "required_artifacts": list(self.required_artifacts),
                "requires_approval": _phase_requires_approval(self.workflow, self.phase),
            },
            "agents": [agent.to_dict() for agent in self.agents],
            "provider_guidance": dict(self.provider_guidance),
            "approvals": dict(self.run.approvals),
            "approval_reasons": dict(self.run.approval_reasons),
            "blocked": {
                "reason": self.run.blocked_reason,
                "at": self.run.blocked_at,
            },
            "phase_history": [dict(item) for item in self.run.phase_history],
            "events": [_event_summary(event) for event in self.events],
            "paths": dict(self.paths),
        }

    def to_markdown(self) -> str:
        lines = [
            "# pal flow phase brief",
            "",
            "## Run",
            "",
            f"- Run ID: `{self.run.run_id}`",
            f"- Feature: `{self.run.feature}`",
            f"- Mode: `{self.run.mode}`",
            f"- Status: `{self.run.status.value}`",
            f"- Phase: `{self.phase.value}`",
            f"- Policy: `{self.policy.value}`",
            f"- Workflow: `{self.run.workflow_name or 'none'}`",
            f"- Work type: `{self.run.work_type or 'none'}`",
            f"- Repos: `{', '.join(self.run.repos) if self.run.repos else 'none'}`",
            f"- Artifact root: `{self.run.artifact_root}`",
            f"- Pal command: `{_shell_command(self.pal_command)}`",
            "",
            "## Artifact Path Rules",
            "",
            "- Artifact names are logical paths resolved under the artifact root.",
            "- Do not create a nested `artifacts/` directory inside the artifact root.",
            (
                f"- Example: `artifacts/example.md` must be written to "
                f"`{self.run.artifact_root}/example.md`."
            ),
            "",
            "## User Request",
            "",
            self.run.request or "No user request recorded.",
            "",
            "## Phase Objective",
            "",
            _phase_objective(self.phase, self.policy),
            "",
            "## Agents",
            "",
            *_agent_lines(self.run, self.agents),
            "",
            "## Required Artifacts",
            "",
            *artifact_lines(
                self.run,
                self.required_artifacts,
                empty="No required artifacts configured.",
            ),
            "",
            "## Provider Guidance",
            "",
            *_provider_lines(self.provider_guidance),
            "",
            "## Tool Delegation",
            "",
            "- Pal does not manage external connector auth for this phase.",
            "- Use your provider-native tools, MCPs, browser tools, and CLIs for GitHub, Linear, Slack, and similar systems when the phase or request requires them.",
            "- Treat agent `tools.required` and `tools.optional` entries as delegated tool expectations, not pal-executed actions.",
            "- Record every external side effect, URL, identifier, and unavailable required tool in the required artifacts.",
            "- If a required external tool is unavailable, mark the phase blocked instead of pretending the action happened.",
            "",
            "## Run State",
            "",
            f"- Approvals: `{', '.join(sorted(self.run.approvals)) if self.run.approvals else 'none'}`",
            f"- Approval reasons: `{_approval_reasons_text(self.run.approval_reasons)}`",
            f"- Blocked reason: `{self.run.blocked_reason or 'none'}`",
            f"- Phase history entries: `{len(self.run.phase_history)}`",
            "",
            *_verification_contract_lines(self.phase),
            "",
            "## Recent Events",
            "",
            *_event_lines(self.events),
            "",
            "## Execution Contract",
            "",
            "- Treat this brief as the source of truth for the current phase.",
            "- Produce the required artifacts before advancing the phase.",
            (
                f"- If blocked, record the blocker with "
                f"`{_shell_command(self.pal_command)} flow block` instead of hiding it in output."
            ),
            (
                f"- When invoking pal from this phase, use "
                f"`{_shell_command(self.pal_command)}` rather than another `pal` binary on PATH."
            ),
        ]
        return "\n".join(lines) + "\n"


def build_phase_brief(
    *,
    run: FlowRun,
    workflow: WorkflowSpec | None,
    events: list[FlowEvent],
    rendered_at: str,
    default_provider: str,
    provider_filter: str = "",
    pal_command: str = "pal",
) -> PhaseBrief:
    workflow_phase = workflow.phase(run.current_phase) if workflow else None
    policy = _phase_policy(run, workflow_phase)
    agents = _resolve_agents(workflow, workflow_phase, default_provider)
    _validate_agents(agents, run.current_phase)
    if provider_filter:
        agents = [agent for agent in agents if agent.provider == provider_filter]
    providers = _brief_providers(
        agents, workflow, workflow_phase, default_provider, provider_filter
    )
    return PhaseBrief(
        run=run,
        phase=run.current_phase,
        policy=policy,
        workflow=workflow,
        agents=agents,
        required_artifacts=list(workflow_phase.required_artifacts if workflow_phase else []),
        events=events,
        provider_guidance={provider: provider_guidance(provider) for provider in providers},
        rendered_at=rendered_at,
        pal_command=pal_command.strip() or "pal",
    )


def provider_guidance(provider: str) -> str:
    if provider == "codex":
        return (
            "Use Codex's native CLI/session harness. Keep writes inside the feature "
            "workspace, preserve the repo instructions, and emit structured progress "
            "that can be copied into phase artifacts."
        )
    if provider == "claude":
        return (
            "Use Claude Code's native project context. Keep permissions aligned with "
            "the phase policy, use plan-oriented behavior for gated phases, and write "
            "results into the requested artifacts."
        )
    return (
        "No provider-specific guidance is configured. Follow the provider-neutral "
        "phase objective and artifact contract."
    )


def _phase_policy(run: FlowRun, workflow_phase: WorkflowPhase | None) -> FlowPolicy:
    if workflow_phase:
        return workflow_phase.policy
    return run.policies.get(run.current_phase.value, FlowPolicy.AUTONOMOUS)


def _resolve_agents(
    workflow: WorkflowSpec | None,
    workflow_phase: WorkflowPhase | None,
    default_provider: str,
) -> list[ResolvedPhaseAgent]:
    if not workflow or not workflow_phase:
        return []
    return [
        _resolve_agent(agent, workflow, workflow_phase, default_provider)
        for agent in workflow_phase.agents
    ]


def _resolve_agent(
    agent: WorkflowAgent,
    workflow: WorkflowSpec,
    phase: WorkflowPhase,
    default_provider: str,
) -> ResolvedPhaseAgent:
    return ResolvedPhaseAgent(
        id=agent.id,
        role=agent.role,
        provider=agent.provider or phase.provider or workflow.defaults.provider or default_provider,
        prompt=agent.prompt,
        produces=list(agent.produces),
        requires=list(agent.requires),
        tools=agent.tools,
    )


def _validate_agents(agents: list[ResolvedPhaseAgent], phase: FlowPhase) -> None:
    for agent in agents:
        if not agent.role.strip() and not agent.prompt.strip():
            raise ValueError(f"Agent '{agent.id}' in phase '{phase.value}' needs a role or prompt.")
        if not agent.provider.strip():
            raise ValueError(f"Agent '{agent.id}' in phase '{phase.value}' needs a provider.")


def _brief_providers(
    agents: list[ResolvedPhaseAgent],
    workflow: WorkflowSpec | None,
    phase: WorkflowPhase | None,
    default_provider: str,
    provider_filter: str,
) -> list[str]:
    if provider_filter:
        return [provider_filter]
    providers = {agent.provider for agent in agents}
    if not providers:
        providers.add(
            (phase.provider if phase else "")
            or (workflow.defaults.provider if workflow else "")
            or default_provider
        )
    return sorted(providers)


def _phase_requires_approval(workflow: WorkflowSpec | None, phase: FlowPhase) -> bool:
    return workflow.phase(phase).requires_approval if workflow else False


def _phase_objective(phase: FlowPhase, policy: FlowPolicy) -> str:
    return (
        f"Execute the `{phase.value}` phase under `{policy.value}` policy. "
        "Keep decisions and outputs explicit enough for the next phase to consume."
    )


def artifact_lines(run: FlowRun, artifacts: list[str], *, empty: str) -> list[str]:
    if not artifacts:
        return [empty]
    return [
        f"- `{artifact}` -> `{resolve_artifact_path(run, Path(run.artifact_root), artifact)}`"
        for artifact in artifacts
    ]


def _verification_contract_lines(phase: FlowPhase) -> list[str]:
    if phase != FlowPhase.VERIFY:
        return []
    return [
        "## Verification Outcome Contract",
        "",
        "Verification artifacts must include a JSON block with `status` set to one of:",
        "",
        "- `passed`: all required checks passed.",
        "- `blocked`: verification could not fully complete; supervisor approval with a reason is required to advance.",
        "- `failed`: verification completed and found a failing check.",
    ]


def _approval_reasons_text(reasons: dict[str, str]) -> str:
    if not reasons:
        return "none"
    return "; ".join(f"{phase}: {reason}" for phase, reason in sorted(reasons.items()))


def _shell_command(command: str) -> str:
    return shlex.quote(command.strip() or "pal")


def _agent_lines(run: FlowRun, agents: list[ResolvedPhaseAgent]) -> list[str]:
    if not agents:
        return ["No agents configured for this phase."]
    lines: list[str] = []
    for agent in agents:
        lines.append(f"- `{agent.id}` ({agent.provider}): {agent.role}")
        if agent.prompt:
            lines.append(f"  Prompt: {agent.prompt}")
        if agent.produces:
            lines.append(f"  Produces: {', '.join(agent.produces)}")
            lines.extend(f"    {line}" for line in artifact_lines(run, agent.produces, empty=""))
        if agent.requires:
            lines.append(f"  Requires: {', '.join(agent.requires)}")
        if agent.tools.required:
            lines.append(f"  Required tools: {', '.join(agent.tools.required)}")
        if agent.tools.optional:
            lines.append(f"  Optional tools: {', '.join(agent.tools.optional)}")
    return lines


def _provider_lines(guidance: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for provider, text in guidance.items():
        lines.append(f"### {provider}")
        lines.append("")
        lines.append(text)
        lines.append("")
    return lines[:-1] if lines else ["No provider guidance rendered."]


def _event_lines(events: list[FlowEvent]) -> list[str]:
    if not events:
        return ["No events recorded."]
    return [f"- `{event.timestamp}` `{event.type}` by `{event.actor}`" for event in events[-10:]]


def _event_summary(event: FlowEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "timestamp": event.timestamp,
        "phase": event.phase.value if event.phase else "",
        "actor": event.actor,
        "summary": str(event.payload.get("summary", "")),
    }
