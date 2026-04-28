from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.artifacts import (
    ArtifactPathError,
    resolve_artifact_path,
    validate_artifact_reference,
    validate_required_artifacts,
)
from pal.flow.models import FlowPhase, FlowPolicy, FlowRun, FlowStatus


def _run(tmp_path: Path) -> FlowRun:
    return FlowRun(
        run_id="run_1",
        feature="feat",
        mode="complex",
        repos=[],
        current_phase=FlowPhase.DESIGN,
        status=FlowStatus.RUNNING,
        policies={"design": FlowPolicy.AUTONOMOUS},
        artifact_root=str(tmp_path / "_wt" / "feat" / ".pal" / "artifacts"),
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )


def test_validate_required_artifacts_resolves_supported_paths(tmp_path: Path) -> None:
    run = _run(tmp_path)
    workspace = tmp_path / "_wt" / "feat"
    (Path(run.artifact_root) / "design.md").parent.mkdir(parents=True)
    (Path(run.artifact_root) / "design.md").write_text("ok\n", encoding="utf-8")

    validation = validate_required_artifacts(
        run,
        workspace_dir=workspace,
        required_artifacts=[
            "artifacts/design.md",
            "design.md",
            "missing.md",
        ],
    )

    assert validation.valid is False
    assert [check.exists for check in validation.checks] == [True, True, False]
    assert validation.missing[0].name == "missing.md"
    assert validation.to_dict()["missing"][0]["name"] == "missing.md"
    assert resolve_artifact_path(run, workspace, "plain.md") == Path(run.artifact_root) / "plain.md"


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("", "must not be empty"),
        ("/tmp/escape.md", "must be relative"),
        ("../escape.md", "must not contain"),
        ("artifacts/../escape.md", "must not contain"),
        ("artifacts", "must name a file"),
        (".", "must name a file"),
    ],
)
def test_artifact_references_are_constrained_to_artifact_root(
    tmp_path: Path,
    artifact: str,
    message: str,
) -> None:
    run = _run(tmp_path)

    with pytest.raises(ArtifactPathError, match=message):
        resolve_artifact_path(run, tmp_path / "_wt" / "feat", artifact)
    with pytest.raises(ArtifactPathError, match=message):
        validate_artifact_reference(artifact)


def test_artifact_resolution_rejects_symlink_escape(tmp_path: Path) -> None:
    run = _run(tmp_path)
    workspace = tmp_path / "_wt" / "feat"
    artifact_root = Path(run.artifact_root)
    artifact_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("not a flow artifact\n", encoding="utf-8")
    (artifact_root / "linked.md").symlink_to(outside)

    with pytest.raises(ArtifactPathError, match="must stay under"):
        resolve_artifact_path(run, workspace, "linked.md")
