from __future__ import annotations

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
    assert "--prompt is required" in result.output


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


def test_flow_providers_preflights_all_registered_providers(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "providers", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "fake" in result.output
    assert "codex" in result.output
    assert "claude" in result.output


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


def test_flow_cfg_helper_accepts_worktree_and_branch_overrides(tmp_path: Path) -> None:
    cfg = flow_cli._cfg_from_ctx(tmp_path, tmp_path / "custom-wt", "bug")

    assert cfg.worktree_root == tmp_path / "custom-wt"
    assert cfg.branch_prefix == "bug"
