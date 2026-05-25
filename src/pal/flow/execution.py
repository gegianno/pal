from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import FlowPhase, FlowRun
from .rendering import PhaseBrief, ResolvedPhaseAgent, artifact_lines
from .workflows.models import WorkflowTools


@dataclass(frozen=True)
class PhaseExecutionTarget:
    agent_id: str
    role: str
    provider: str
    prompt: str
    produces: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    tools: WorkflowTools = field(default_factory=WorkflowTools)
    synthetic: bool = False

    @classmethod
    def from_agent(cls, agent: ResolvedPhaseAgent) -> PhaseExecutionTarget:
        return cls(
            agent_id=agent.id,
            role=agent.role,
            provider=agent.provider,
            prompt=agent.prompt,
            produces=list(agent.produces),
            requires=list(agent.requires),
            tools=agent.tools,
        )

    @classmethod
    def phase(cls, *, provider: str) -> PhaseExecutionTarget:
        return cls(
            agent_id="phase",
            role="phase executor",
            provider=provider,
            prompt="Execute the current phase from the rendered brief.",
            synthetic=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "provider": self.provider,
            "prompt": self.prompt,
            "produces": list(self.produces),
            "requires": list(self.requires),
            "tools": self.tools.to_dict(),
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class PhaseExecutionRecord:
    execution_id: str
    phase: FlowPhase
    target: PhaseExecutionTarget
    execution_mode: str
    status: str
    returncode: int
    command: list[str]
    cwd: str
    started_at: str
    ended_at: str
    paths: dict[str, str]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "phase": self.phase.value,
            "target": self.target.to_dict(),
            "execution_mode": self.execution_mode,
            "status": self.status,
            "returncode": self.returncode,
            "command": list(self.command),
            "cwd": self.cwd,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "paths": dict(self.paths),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class PhaseExecutionSummary:
    run: FlowRun
    phase: FlowPhase
    status: str
    brief: PhaseBrief
    executions: list[PhaseExecutionRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run.run_id,
            "phase": self.phase.value,
            "status": self.status,
            "brief_paths": dict(self.brief.paths),
            "executions": [execution.to_dict() for execution in self.executions],
        }


def phase_execution_targets(
    brief: PhaseBrief,
    *,
    agent_id: str = "",
) -> list[PhaseExecutionTarget]:
    targets = [PhaseExecutionTarget.from_agent(agent) for agent in brief.agents]
    if agent_id:
        filtered = [target for target in targets if target.agent_id == agent_id]
        if filtered:
            return filtered
        raise ValueError(f"Phase '{brief.phase.value}' has no rendered agent '{agent_id}'.")
    if targets:
        return targets
    if _phase_has_configured_agents(brief):
        raise ValueError(
            f"Phase '{brief.phase.value}' has no rendered agents after provider filtering."
        )
    return [
        PhaseExecutionTarget.phase(provider=provider)
        for provider in sorted(brief.provider_guidance)
    ]


def build_execution_prompt(brief: PhaseBrief, target: PhaseExecutionTarget) -> str:
    lines = [
        "# pal flow execution request",
        "",
        "## Target",
        "",
        f"- Agent ID: `{target.agent_id}`",
        f"- Provider: `{target.provider}`",
        f"- Role: `{target.role}`",
        f"- Synthetic target: `{str(target.synthetic).lower()}`",
        "",
        "## Agent Instructions",
        "",
        target.prompt,
        "",
        "## Produces",
        "",
        *artifact_lines(
            brief.run,
            target.produces,
            empty="No agent-specific produced artifacts configured.",
        ),
        "",
        "## Requires",
        "",
        *_list_lines(target.requires, empty="No agent-specific requirements configured."),
        "",
        "## Tool Expectations",
        "",
        *_tool_expectation_lines(target.tools),
        "",
        "## Rendered Phase Brief",
        "",
        brief.to_markdown().rstrip(),
        "",
    ]
    return "\n".join(lines)


def redact_command_prompt(command: list[str], prompt: str) -> list[str]:
    if not prompt:
        return list(command)
    return ["<prompt>" if item == prompt else item for item in command]


def execution_status(records: list[PhaseExecutionRecord]) -> str:
    return (
        "completed"
        if records
        and all(record.status == "completed" and record.returncode == 0 for record in records)
        else "failed"
    )


def _list_lines(items: list[str], *, empty: str) -> list[str]:
    return [f"- `{item}`" for item in items] if items else [empty]


def _tool_expectation_lines(tools: WorkflowTools) -> list[str]:
    if not tools.required and not tools.optional:
        return ["No agent-specific tool expectations configured."]
    lines: list[str] = []
    if tools.required:
        lines.append("Required tools:")
        lines.extend(f"- `{tool}`" for tool in tools.required)
    if tools.optional:
        lines.append("Optional tools:")
        lines.extend(f"- `{tool}`" for tool in tools.optional)
    return lines


def _phase_has_configured_agents(brief: PhaseBrief) -> bool:
    return bool(brief.workflow and brief.workflow.phase(brief.phase).agents)
