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


@dataclass(frozen=True)
class WorkflowTransition:
    on: str
    to: FlowPhase

    def to_dict(self) -> dict[str, str]:
        return {"on": self.on, "to": self.to.value}


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


@dataclass(frozen=True)
class WorkflowValidationResult:
    path: Path
    name: str
    work_type: str
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors
