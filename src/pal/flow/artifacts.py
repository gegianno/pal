from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any

from .models import FlowPhase, FlowRun


class ArtifactPathError(ValueError):
    pass


class VerificationStatus(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"
    FAILED = "failed"


class VerificationStatusError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactCheck:
    name: str
    path: str
    exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
        }


@dataclass(frozen=True)
class ArtifactValidation:
    run: FlowRun
    phase: FlowPhase
    required: list[str]
    checks: list[ArtifactCheck]

    @property
    def missing(self) -> list[ArtifactCheck]:
        return [check for check in self.checks if not check.exists]

    @property
    def valid(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run.run_id,
            "phase": self.phase.value,
            "valid": self.valid,
            "required": list(self.required),
            "missing": [check.to_dict() for check in self.missing],
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class VerificationOutcome:
    artifact: str
    path: str
    status: VerificationStatus
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "path": self.path,
            "status": self.status.value,
            "payload": dict(self.payload),
        }


def validate_required_artifacts(
    run: FlowRun,
    *,
    workspace_dir: Path,
    required_artifacts: list[str],
) -> ArtifactValidation:
    checks: list[ArtifactCheck] = []
    for artifact in required_artifacts:
        path = resolve_artifact_path(run, workspace_dir, artifact)
        checks.append(ArtifactCheck(name=artifact, path=str(path), exists=path.is_file()))
    return ArtifactValidation(
        run=run,
        phase=run.current_phase,
        required=list(required_artifacts),
        checks=checks,
    )


def read_verification_outcome(
    run: FlowRun,
    *,
    workspace_dir: Path,
    artifact: str = "artifacts/verification.md",
) -> VerificationOutcome:
    path = resolve_artifact_path(run, workspace_dir, artifact)
    if not path.is_file():
        raise VerificationStatusError(f"Verification artifact is missing: {artifact}")
    payload = _verification_payload(path.read_text(encoding="utf-8"))
    raw_status = str(payload.get("status", "")).strip()
    if not raw_status:
        raise VerificationStatusError(
            f"Verification artifact '{artifact}' must declare status as one of: "
            f"{', '.join(status.value for status in VerificationStatus)}."
        )
    try:
        status = VerificationStatus(raw_status)
    except ValueError as exc:
        raise VerificationStatusError(
            f"Verification artifact '{artifact}' has unsupported status '{raw_status}'. "
            f"Expected one of: {', '.join(status.value for status in VerificationStatus)}."
        ) from exc
    return VerificationOutcome(
        artifact=artifact,
        path=str(path),
        status=status,
        payload=payload,
    )


def resolve_artifact_path(run: FlowRun, workspace_dir: Path, artifact: str) -> Path:
    del workspace_dir
    root = Path(run.artifact_root).resolve()
    path = root.joinpath(*_artifact_relative_path(artifact).parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ArtifactPathError(
            f"Artifact path must stay under the run artifact directory: {artifact}"
        ) from exc
    return path


def is_verification_artifact(artifact: str) -> bool:
    return _artifact_relative_path(artifact).as_posix() == "verification.md"


def validate_artifact_reference(artifact: str) -> None:
    _artifact_relative_path(artifact)


def _verification_payload(text: str) -> dict[str, Any]:
    for candidate in reversed(re.findall(r"```json\s*(.*?)```", text, flags=re.DOTALL)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "status" in parsed:
            return parsed
    match = re.search(r"(?im)^\s*status\s*[:=]\s*`?([a-z_]+)`?\s*$", text)
    if match:
        return {"status": match.group(1)}
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _artifact_relative_path(artifact: str) -> Path:
    value = artifact.strip()
    if not value:
        raise ArtifactPathError("Artifact path must not be empty.")
    path = Path(value)
    if path.is_absolute():
        raise ArtifactPathError(f"Artifact path must be relative: {artifact}")
    parts = path.parts
    if any(part == ".." for part in parts):
        raise ArtifactPathError(f"Artifact path must not contain '..': {artifact}")
    if parts[:1] == ("artifacts",):
        parts = parts[1:]
    if not parts or any(part in {"", "."} for part in parts):
        raise ArtifactPathError(f"Artifact path must name a file under artifacts: {artifact}")
    return Path(*parts)
