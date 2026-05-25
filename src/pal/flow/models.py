from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class FlowPhase(str, Enum):
    EXPLORE = "explore"
    DESIGN = "design"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    PR = "pr"
    REVIEW = "review"


class FlowPolicy(str, Enum):
    OBSERVER = "observer"
    SUPERVISOR = "supervisor"
    CO_DRIVER = "co-driver"
    AUTONOMOUS = "autonomous"


class FlowStatus(str, Enum):
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True)
class FlowRun:
    run_id: str
    feature: str
    mode: str
    repos: list[str]
    current_phase: FlowPhase
    status: FlowStatus
    policies: dict[str, FlowPolicy]
    artifact_root: str
    created_at: str
    updated_at: str
    pr_urls: list[str] = field(default_factory=list)
    workflow_name: str = ""
    work_type: str = ""
    phase_history: list[dict[str, Any]] = field(default_factory=list)
    approvals: dict[str, str] = field(default_factory=dict)
    approval_reasons: dict[str, str] = field(default_factory=dict)
    blocked_reason: str = ""
    blocked_at: str = ""
    request: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "feature": self.feature,
            "mode": self.mode,
            "repos": list(self.repos),
            "current_phase": self.current_phase.value,
            "status": self.status.value,
            "policies": {phase: policy.value for phase, policy in self.policies.items()},
            "artifact_root": self.artifact_root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pr_urls": list(self.pr_urls),
            "workflow_name": self.workflow_name,
            "work_type": self.work_type,
            "phase_history": [dict(item) for item in self.phase_history],
            "approvals": dict(self.approvals),
            "approval_reasons": dict(self.approval_reasons),
            "blocked_reason": self.blocked_reason,
            "blocked_at": self.blocked_at,
            "request": self.request,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowRun:
        return cls(
            run_id=str(data["run_id"]),
            feature=str(data["feature"]),
            mode=str(data["mode"]),
            repos=[str(repo) for repo in data["repos"]],
            current_phase=FlowPhase(str(data["current_phase"])),
            status=FlowStatus(str(data["status"])),
            policies={
                str(phase): FlowPolicy(str(policy))
                for phase, policy in data.get("policies", {}).items()
            },
            artifact_root=str(data["artifact_root"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            pr_urls=[str(url) for url in data.get("pr_urls", [])],
            workflow_name=str(data.get("workflow_name", "")),
            work_type=str(data.get("work_type", "")),
            phase_history=[
                dict(item) for item in data.get("phase_history", []) if isinstance(item, dict)
            ],
            approvals={
                str(phase): str(timestamp) for phase, timestamp in data.get("approvals", {}).items()
            },
            approval_reasons={
                str(phase): str(reason)
                for phase, reason in data.get("approval_reasons", {}).items()
            },
            blocked_reason=str(data.get("blocked_reason", "")),
            blocked_at=str(data.get("blocked_at", "")),
            request=str(data.get("request", "")),
        )


@dataclass(frozen=True)
class FlowEvent:
    id: str
    run_id: str
    type: str
    timestamp: str
    phase: Optional[FlowPhase]
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    causation_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "phase": self.phase.value if self.phase else None,
            "actor": self.actor,
            "payload": dict(self.payload),
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowEvent:
        phase = data.get("phase")
        return cls(
            id=str(data["id"]),
            run_id=str(data["run_id"]),
            type=str(data["type"]),
            timestamp=str(data["timestamp"]),
            phase=FlowPhase(str(phase)) if phase else None,
            actor=str(data["actor"]),
            payload=dict(data.get("payload", {})),
            causation_id=data.get("causation_id"),
            correlation_id=data.get("correlation_id"),
        )
