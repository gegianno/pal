from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import pal.flow.cli as flow_cli
from pal.cli import app
from pal.flow.events import FlowEventLog
from pal.flow.models import FlowEvent, FlowPhase
from pal.flow.store import LocalFlowStore


runner = CliRunner()


def _plain(output: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


def _write_workflow(root: Path, name: str, body: str) -> Path:
    path = root / ".pal" / "flows" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _event_types(root: Path, feature: str) -> list[str]:
    store = LocalFlowStore(root / "_wt")
    run_id = store.latest_run_id(feature)
    return [event.type for event in FlowEventLog(store.events_path(feature, run_id)).read()]


def _valid_workflow() -> str:
    return """
version: 1
name: dev-complex
work_type: dev
mode: complex
repos:
  - api
defaults:
  provider: fake
phases:
  - id: design
    policy: co-driver
    agents:
      - id: designer
        role: design
        requires:
          - local_headless
  - id: implement
    agents: []
"""


def _gated_workflow() -> str:
    return """
version: 1
name: gated
work_type: dev
defaults:
  provider: fake
phases:
  - id: design
    requires_approval: true
    transitions:
      - on: complete
        to: implement
    agents: []
  - id: implement
    transitions:
      - on: blocked
        to: design
    agents: []
"""


def test_flow_start_status_and_watch_commands(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "flow",
            "start",
            "feat",
            "--root",
            str(tmp_path),
            "--repo",
            "api",
            "--repo",
            "web",
            "--mode",
            "complex",
            "--phase",
            "design",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pal flow started" in result.output

    status = runner.invoke(app, ["flow", "status", "feat", "--root", str(tmp_path)])
    assert status.exit_code == 0, status.output
    assert "complex" in status.output
    assert "design" in status.output
    assert "api, web" in status.output

    watch = runner.invoke(app, ["flow", "watch", "feat", "--root", str(tmp_path)])
    assert watch.exit_code == 0, watch.output
    assert "flow.run.started" in watch.output
    assert "provider.started" in watch.output


def test_flow_status_explicit_run_id_and_empty_repo_display(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output
    run_id = (tmp_path / "_wt" / "feat" / ".pal" / "runs" / "latest").read_text(encoding="utf-8")

    status = runner.invoke(
        app,
        ["flow", "status", "feat", "--root", str(tmp_path), "--run-id", run_id.strip()],
    )

    assert status.exit_code == 0, status.output
    assert "(none)" in status.output


def test_flow_start_rejects_unknown_phase(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--phase", "unknown"],
    )

    assert result.exit_code != 0
    assert "Unknown phase" in result.output


def test_flow_start_rejects_unknown_provider(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--provider", "missing"],
    )

    assert result.exit_code != 0
    assert "Unknown provider" in result.output


def test_flow_start_requires_prompt_for_headless_runs(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--provider", "fake", "--headless"],
    )

    assert result.exit_code != 0
    assert "--prompt is required" in _plain(result.output)


def test_flow_start_headless_fake_records_completion(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "flow",
            "start",
            "feat",
            "--root",
            str(tmp_path),
            "--provider",
            "fake",
            "--headless",
            "--prompt",
            "hello",
        ],
    )
    assert result.exit_code == 0, result.output

    watch = runner.invoke(app, ["flow", "watch", "feat", "--root", str(tmp_path)])
    assert watch.exit_code == 0, watch.output
    assert "provider.completed" in watch.output


def test_flow_start_with_workflow_uses_spec_defaults(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())
    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "dev-complex"],
    )
    assert result.exit_code == 0, result.output

    status = runner.invoke(app, ["flow", "status", "feat", "--root", str(tmp_path)])
    assert status.exit_code == 0, status.output
    assert "dev-complex" in status.output
    assert "dev" in status.output
    assert "complex" in status.output
    assert "design" in status.output
    assert "api" in status.output
    assert "phase_history" in status.output


def test_flow_start_rejects_invalid_workflow(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "bad",
        """
version: 1
name: bad
work_type: dev
defaults:
  provider: missing
phases:
  - id: explore
    agents: []
""",
    )

    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "bad"],
    )

    assert result.exit_code != 0
    assert "Workflow spec is invalid" in _plain(result.output)


def test_flow_providers_preflights_all_registered_providers(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "providers", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "fake" in result.output
    assert "codex" in result.output
    assert "claude" in result.output


def test_flow_validate_reports_no_specs(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "validate", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No workflow specs found" in result.output


def test_flow_validate_reports_valid_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())

    result = runner.invoke(app, ["flow", "validate", "dev-complex", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "dev-complex" in result.output
    assert "dev" in result.output
    assert "valid" in result.output


def test_flow_validate_reports_invalid_workflow_and_exits_nonzero(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "bad",
        """
version: 1
name: bad
work_type: dev
defaults:
  provider: missing
phases:
  - id: explore
    agents: []
""",
    )

    result = runner.invoke(app, ["flow", "validate", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "invalid" in result.output
    assert "Unknown provider" in result.output


def test_flow_advance_default_run_updates_phase(tmp_path: Path) -> None:
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--phase", "review"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(app, ["flow", "advance", "feat", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    status = runner.invoke(app, ["flow", "status", "feat", "--root", str(tmp_path)])

    assert "pal flow advanced" in result.output
    assert "ship" in status.output


def test_flow_advance_reports_service_errors(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        ["flow", "advance", "feat", "--root", str(tmp_path), "--on", "blocked"],
    )

    assert result.exit_code != 0


def test_flow_approve_and_advance_gated_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "gated", _gated_workflow())
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path), "-w", "gated"])
    assert start.exit_code == 0, start.output

    blocked_advance = runner.invoke(app, ["flow", "advance", "feat", "--root", str(tmp_path)])
    assert blocked_advance.exit_code != 0
    assert "requires approval" in _plain(blocked_advance.output)

    approve = runner.invoke(app, ["flow", "approve", "feat", "--root", str(tmp_path)])
    status_after_approve = runner.invoke(app, ["flow", "status", "feat", "--root", str(tmp_path)])
    advance = runner.invoke(app, ["flow", "advance", "feat", "--root", str(tmp_path)])

    assert approve.exit_code == 0, approve.output
    assert "pal flow approved" in approve.output
    assert "approvals" in status_after_approve.output
    assert "design" in status_after_approve.output
    assert advance.exit_code == 0, advance.output
    assert "implement" in advance.output
    assert _event_types(tmp_path, "feat")[-2:] == [
        "flow.phase.approved",
        "flow.phase.advanced",
    ]


def test_flow_approve_rejects_unknown_phase(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "approve", "feat", "--root", str(tmp_path), "--phase", "unknown"],
    )

    assert result.exit_code != 0
    assert "Unknown phase" in result.output


def test_flow_block_and_replan_commands(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "gated", _gated_workflow())
    start = runner.invoke(
        app,
        [
            "flow",
            "start",
            "feat",
            "--root",
            str(tmp_path),
            "-w",
            "gated",
            "--phase",
            "implement",
        ],
    )
    assert start.exit_code == 0, start.output

    block = runner.invoke(
        app,
        ["flow", "block", "feat", "--root", str(tmp_path), "--reason", "tests failed"],
    )
    status = runner.invoke(app, ["flow", "status", "feat", "--root", str(tmp_path)])
    replan = runner.invoke(
        app,
        ["flow", "replan", "feat", "--root", str(tmp_path), "--reason", "redesign"],
    )

    assert block.exit_code == 0, block.output
    assert "blocked" in status.output
    assert "tests failed" in status.output
    assert replan.exit_code == 0, replan.output
    assert "design" in replan.output
    assert _event_types(tmp_path, "feat")[-2:] == [
        "flow.phase.blocked",
        "flow.phase.replanned",
    ]


def test_flow_replan_accepts_explicit_phase_and_rejects_unknown_phase(tmp_path: Path) -> None:
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--phase", "implement"],
    )
    assert start.exit_code == 0, start.output

    explicit = runner.invoke(
        app,
        ["flow", "replan", "feat", "--root", str(tmp_path), "--phase", "verify"],
    )
    bad = runner.invoke(
        app,
        ["flow", "replan", "feat", "--root", str(tmp_path), "--phase", "unknown"],
    )

    assert explicit.exit_code == 0, explicit.output
    assert "verify" in explicit.output
    assert bad.exit_code != 0
    assert "Unknown phase" in bad.output


def test_flow_block_requires_non_blank_reason(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        ["flow", "block", "feat", "--root", str(tmp_path), "--reason", " "],
    )

    assert result.exit_code != 0
    assert "Block reason is required" in _plain(result.output)


def test_flow_status_reports_missing_run_as_bad_parameter(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "status", "missing", "--root", str(tmp_path)])

    assert result.exit_code != 0
    assert "No flow runs found" in result.output


def test_flow_watch_handles_run_with_no_events(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    service = flow_cli.build_local_flow_service(flow_cli._cfg_from_ctx(tmp_path, None, None))
    run = service.start(feature="feat", repos=[], mode="routine", phase=FlowPhase.EXPLORE)
    FlowEventLog(store.events_path("feat", run.run_id)).path.write_text("", encoding="utf-8")

    result = runner.invoke(app, ["flow", "watch", "feat", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No events recorded" in result.output


def test_flow_watch_prints_blank_phase_when_event_has_no_phase(tmp_path: Path) -> None:
    service = flow_cli.build_local_flow_service(flow_cli._cfg_from_ctx(tmp_path, None, None))
    run = service.start(feature="feat", repos=[], mode="routine", phase=FlowPhase.EXPLORE)
    store = LocalFlowStore(tmp_path / "_wt")
    log = FlowEventLog(store.events_path("feat", run.run_id))
    log.path.write_text("", encoding="utf-8")
    log.append(
        FlowEvent(
            id="evt_no_phase",
            run_id=run.run_id,
            type="custom",
            timestamp="2026-04-27T00:00:00Z",
            phase=None,
            actor="test",
        )
    )

    result = runner.invoke(app, ["flow", "watch", "feat", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "custom" in result.output
    assert "test" in result.output


def test_flow_cli_helpers_parse_and_raise() -> None:
    assert flow_cli._parse_phase("ship") == FlowPhase.SHIP

    with pytest.raises(typer.BadParameter, match="Unknown phase"):
        flow_cli._parse_phase("bad")

    class MissingService:
        def status(self, _feature: str, _run_id: str | None):  # noqa: ANN201
            raise FileNotFoundError("missing")

    with pytest.raises(typer.BadParameter, match="missing"):
        flow_cli._load_run_or_error(MissingService(), "feat", None)

    class ChangingService:
        def ok(self) -> str:
            return "changed"

        def bad(self) -> None:
            raise ValueError("bad change")

    assert flow_cli._change_or_error(ChangingService(), "ok") == "changed"
    with pytest.raises(typer.BadParameter, match="bad change"):
        flow_cli._change_or_error(ChangingService(), "bad")


def test_flow_cfg_helper_accepts_worktree_and_branch_overrides(tmp_path: Path) -> None:
    cfg = flow_cli._cfg_from_ctx(tmp_path, tmp_path / "custom-wt", "bug")

    assert cfg.worktree_root == tmp_path / "custom-wt"
    assert cfg.branch_prefix == "bug"
