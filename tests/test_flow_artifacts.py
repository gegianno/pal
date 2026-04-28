from __future__ import annotations

from pathlib import Path

from pal.flow.artifacts import resolve_artifact_path, validate_required_artifacts
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
    (workspace / ".pal" / "custom.md").write_text("ok\n", encoding="utf-8")
    absolute = tmp_path / "absolute.md"
    absolute.write_text("ok\n", encoding="utf-8")

    validation = validate_required_artifacts(
        run,
        workspace_dir=workspace,
        required_artifacts=[
            "artifacts/design.md",
            ".pal/custom.md",
            str(absolute),
            "missing.md",
        ],
    )

    assert validation.valid is False
    assert [check.exists for check in validation.checks] == [True, True, True, False]
    assert validation.missing[0].name == "missing.md"
    assert validation.to_dict()["missing"][0]["name"] == "missing.md"
    assert resolve_artifact_path(run, workspace, "plain.md") == Path(run.artifact_root) / "plain.md"
