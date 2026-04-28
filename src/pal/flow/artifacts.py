from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import FlowPhase, FlowRun


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


def validate_required_artifacts(
    run: FlowRun,
    *,
    workspace_dir: Path,
    required_artifacts: list[str],
) -> ArtifactValidation:
    checks = [
        ArtifactCheck(
            name=artifact,
            path=str(resolve_artifact_path(run, workspace_dir, artifact)),
            exists=resolve_artifact_path(run, workspace_dir, artifact).is_file(),
        )
        for artifact in required_artifacts
    ]
    return ArtifactValidation(
        run=run,
        phase=run.current_phase,
        required=list(required_artifacts),
        checks=checks,
    )


def resolve_artifact_path(run: FlowRun, workspace_dir: Path, artifact: str) -> Path:
    path = Path(artifact)
    if path.is_absolute():
        return path
    if artifact.startswith(".pal/"):
        return workspace_dir / path
    if path.parts[:1] == ("artifacts",):
        return Path(run.artifact_root).joinpath(*path.parts[1:])
    return Path(run.artifact_root) / path
