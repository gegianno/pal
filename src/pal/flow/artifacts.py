from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import FlowPhase, FlowRun


class ArtifactPathError(ValueError):
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


def validate_artifact_reference(artifact: str) -> None:
    _artifact_relative_path(artifact)


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
