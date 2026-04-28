from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import FlowPhase, FlowPolicy


@dataclass(frozen=True)
class WorkflowAgent:
    id: str
    role: str
    provider: str
    prompt: str
    produces: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "provider": self.provider,
            "prompt": self.prompt,
            "produces": list(self.produces),
            "requires": list(self.requires),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowAgent:
        return cls(
            id=str(data["id"]),
            role=str(data["role"]),
            provider=str(data.get("provider", "")),
            prompt=str(data.get("prompt", "")),
            produces=[str(item) for item in data.get("produces", [])],
            requires=[str(item) for item in data.get("requires", [])],
        )


@dataclass(frozen=True)
class WorkflowTransition:
    on: str
    to: FlowPhase

    def to_dict(self) -> dict[str, str]:
        return {"on": self.on, "to": self.to.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowTransition:
        return cls(on=str(data["on"]), to=FlowPhase(str(data["to"])))


@dataclass(frozen=True)
class WorkflowPhase:
    id: FlowPhase
    policy: FlowPolicy
    agents: list[WorkflowAgent]
    required_artifacts: list[str] = field(default_factory=list)
    transitions: list[WorkflowTransition] = field(default_factory=list)
    requires_approval: bool = False
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "policy": self.policy.value,
            "provider": self.provider,
            "agents": [agent.to_dict() for agent in self.agents],
            "required_artifacts": list(self.required_artifacts),
            "transitions": [transition.to_dict() for transition in self.transitions],
            "requires_approval": self.requires_approval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowPhase:
        return cls(
            id=FlowPhase(str(data["id"])),
            policy=FlowPolicy(str(data["policy"])),
            provider=str(data.get("provider", "")),
            agents=[WorkflowAgent.from_dict(agent) for agent in data.get("agents", [])],
            required_artifacts=[str(item) for item in data.get("required_artifacts", [])],
            transitions=[
                WorkflowTransition.from_dict(transition)
                for transition in data.get("transitions", [])
            ],
            requires_approval=bool(data.get("requires_approval", False)),
        )


@dataclass(frozen=True)
class WorkflowDefaults:
    provider: str = ""
    policy: FlowPolicy | None = None

    def to_dict(self) -> dict[str, str]:
        data: dict[str, str] = {}
        if self.provider:
            data["provider"] = self.provider
        if self.policy:
            data["policy"] = self.policy.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefaults:
        policy = data.get("policy")
        return cls(
            provider=str(data.get("provider", "")),
            policy=FlowPolicy(str(policy)) if policy else None,
        )


@dataclass(frozen=True)
class WorkflowSpec:
    version: int
    name: str
    work_type: str
    phases: list[WorkflowPhase]
    path: Path
    description: str = ""
    mode: str = "complex"
    repos: list[str] = field(default_factory=list)
    defaults: WorkflowDefaults = field(default_factory=WorkflowDefaults)

    @property
    def initial_phase(self) -> FlowPhase:
        return self.phases[0].id

    def policies_by_phase(self) -> dict[str, FlowPolicy]:
        return {phase.id.value: phase.policy for phase in self.phases}

    def phase(self, phase_id: FlowPhase) -> WorkflowPhase:
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        raise ValueError(f"Workflow '{self.name}' has no phase '{phase_id.value}'.")

    def phase_after(self, phase_id: FlowPhase) -> FlowPhase | None:
        phase_ids = [phase.id for phase in self.phases]
        try:
            index = phase_ids.index(phase_id)
        except ValueError as exc:
            raise ValueError(f"Workflow '{self.name}' has no phase '{phase_id.value}'.") from exc
        next_index = index + 1
        return phase_ids[next_index] if next_index < len(phase_ids) else None

    def transition_target(self, phase_id: FlowPhase, signal: str) -> FlowPhase | None:
        phase = self.phase(phase_id)
        for transition in phase.transitions:
            if transition.on == signal:
                return transition.to
        if signal == "complete":
            return self.phase_after(phase_id)
        return None

    def referenced_providers(self, fallback_provider: str) -> set[str]:
        providers = {self.defaults.provider or fallback_provider}
        for phase in self.phases:
            if phase.provider:
                providers.add(phase.provider)
            for agent in phase.agents:
                providers.add(
                    agent.provider or phase.provider or self.defaults.provider or fallback_provider
                )
        return providers

    def to_run_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "work_type": self.work_type,
            "description": self.description,
            "mode": self.mode,
            "repos": list(self.repos),
            "defaults": self.defaults.to_dict(),
            "initial_phase": self.initial_phase.value,
            "path": str(self.path),
            "phases": [phase.to_dict() for phase in self.phases],
        }

    @classmethod
    def from_run_dict(cls, data: dict[str, Any]) -> WorkflowSpec:
        return cls(
            version=int(data["version"]),
            name=str(data["name"]),
            work_type=str(data["work_type"]),
            description=str(data.get("description", "")),
            mode=str(data.get("mode", "complex")),
            repos=[str(repo) for repo in data.get("repos", [])],
            defaults=WorkflowDefaults.from_dict(data.get("defaults", {})),
            phases=[WorkflowPhase.from_dict(phase) for phase in data["phases"]],
            path=Path(str(data.get("path", ""))),
        )


@dataclass(frozen=True)
class WorkflowValidationResult:
    path: Path
    name: str
    work_type: str
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors
