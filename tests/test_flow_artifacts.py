from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.artifacts import (
    ArtifactPathError,
    VerificationStatus,
    VerificationStatusError,
    is_verification_artifact,
    read_verification_outcome,
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


def test_read_verification_outcome_reads_standard_json_status(tmp_path: Path) -> None:
    run = _run(tmp_path)
    workspace = tmp_path / "_wt" / "feat"
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        '# Verification\n\n```json\n{"status": "blocked", "reason": "browser"}\n```\n',
        encoding="utf-8",
    )

    outcome = read_verification_outcome(
        run,
        workspace_dir=workspace,
        artifact="artifacts/verification.md",
    )

    assert outcome.status == VerificationStatus.BLOCKED
    assert outcome.payload["reason"] == "browser"
    assert outcome.path == str(artifact.resolve())
    assert outcome.to_dict()["status"] == "blocked"
    assert is_verification_artifact("artifacts/verification.md") is True
    assert is_verification_artifact("verification.md") is True
    assert is_verification_artifact("artifacts/regression.md") is False


def test_read_verification_outcome_accepts_status_line(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("status: failed\n", encoding="utf-8")

    outcome = read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")

    assert outcome.status == VerificationStatus.FAILED


def test_read_verification_outcome_accepts_whole_json_document(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"status": "blocked", "checks": []}', encoding="utf-8")

    outcome = read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")

    assert outcome.status == VerificationStatus.BLOCKED
    assert outcome.payload["checks"] == []


def test_read_verification_outcome_ignores_unusable_json_blocks(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        '```json\n{"detail": "missing status"}\n```\n```json\nnot json\n```\nstatus: passed\n',
        encoding="utf-8",
    )

    outcome = read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")

    assert outcome.status == VerificationStatus.PASSED


def test_read_verification_outcome_rejects_missing_file(tmp_path: Path) -> None:
    run = _run(tmp_path)

    with pytest.raises(VerificationStatusError, match="Verification artifact is missing"):
        read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")


def test_read_verification_outcome_ignores_whole_json_non_object(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('["passed"]', encoding="utf-8")

    with pytest.raises(VerificationStatusError, match="must declare status"):
        read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")


def test_read_verification_outcome_rejects_missing_or_custom_status(tmp_path: Path) -> None:
    run = _run(tmp_path)
    artifact = Path(run.artifact_root) / "verification.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        '```json\n{"status": "passed_with_browser_blocked_by_sandbox"}\n```',
        encoding="utf-8",
    )

    with pytest.raises(VerificationStatusError, match="unsupported status"):
        read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")

    artifact.write_text("No structured status.\n", encoding="utf-8")
    with pytest.raises(VerificationStatusError, match="must declare status"):
        read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")

    artifact.write_text("{not json", encoding="utf-8")
    with pytest.raises(VerificationStatusError, match="must declare status"):
        read_verification_outcome(run, workspace_dir=tmp_path / "_wt" / "feat")
