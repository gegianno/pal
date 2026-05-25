from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..identifiers import normalize_identifier
from .models import FlowPhase


class EvidenceStatus(str, Enum):
    PASSED = "passed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class FlowEvidence:
    evidence_id: str
    run_id: str
    phase: FlowPhase
    check: str
    status: EvidenceStatus
    summary: str
    details: str
    url: str
    artifacts: list[str]
    actor: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "run_id": self.run_id,
            "phase": self.phase.value,
            "check": self.check,
            "status": self.status.value,
            "summary": self.summary,
            "details": self.details,
            "url": self.url,
            "artifacts": list(self.artifacts),
            "actor": self.actor,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowEvidence:
        return cls(
            evidence_id=str(data["evidence_id"]),
            run_id=str(data["run_id"]),
            phase=FlowPhase(str(data["phase"])),
            check=normalize_evidence_check(str(data["check"])),
            status=_evidence_status(data["status"]),
            summary=str(data["summary"]),
            details=str(data.get("details", "")),
            url=str(data.get("url", "")),
            artifacts=_evidence_artifacts(data),
            actor=str(data.get("actor", "")),
            created_at=str(data["created_at"]),
        )


@dataclass(frozen=True)
class EvidenceRequirement:
    phase: FlowPhase
    check: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "check": self.check,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FlowReadiness:
    run_id: str
    status: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requirements: list[EvidenceRequirement] = field(default_factory=list)
    evidence: list[FlowEvidence] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "requirements": [requirement.to_dict() for requirement in self.requirements],
            "evidence": [record.to_dict() for record in self.evidence],
        }


def normalize_evidence_check(value: str) -> str:
    return normalize_identifier(value, "Evidence check")


def _evidence_artifacts(data: dict[str, Any]) -> list[str]:
    if "artifacts" in data:
        artifacts = data.get("artifacts", [])
        if isinstance(artifacts, list):
            return [str(artifact) for artifact in artifacts]
    artifact = str(data.get("artifact", "")).strip()
    return [artifact] if artifact else []


def _evidence_status(value: Any) -> EvidenceStatus:
    raw_status = str(value)
    if raw_status == "waived":
        return EvidenceStatus.SKIPPED
    return EvidenceStatus(raw_status)
