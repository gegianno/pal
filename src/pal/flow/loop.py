from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .artifacts import ArtifactValidation
from .execution import PhaseExecutionSummary
from .models import FlowPhase, FlowPolicy, FlowRun


@dataclass(frozen=True)
class FlowRunStep:
    phase: FlowPhase
    policy: FlowPolicy
    status: str
    message: str
    execution: PhaseExecutionSummary | None = None
    artifacts: ArtifactValidation | None = None
    advanced_to: FlowPhase | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "policy": self.policy.value,
            "status": self.status,
            "message": self.message,
            "execution": self.execution.to_dict() if self.execution else None,
            "artifacts": self.artifacts.to_dict() if self.artifacts else None,
            "advanced_to": self.advanced_to.value if self.advanced_to else "",
        }


@dataclass(frozen=True)
class FlowRunLoopSummary:
    run: FlowRun
    status: str
    steps: list[FlowRunStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run.run_id,
            "status": self.status,
            "phase": self.run.current_phase.value,
            "run_status": self.run.status.value,
            "steps": [step.to_dict() for step in self.steps],
        }
