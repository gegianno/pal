from __future__ import annotations

import re
from pathlib import Path
import subprocess

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


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "pal@example.com")
    _git(path, "config", "user.name", "pal")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")
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


def _artifact_workflow() -> str:
    return """
version: 1
name: artifact
work_type: dev
defaults:
  provider: fake
phases:
  - id: design
    policy: autonomous
    required_artifacts:
      - artifacts/design.md
    transitions:
      - on: complete
        to: implement
    agents: []
  - id: implement
    agents: []
"""


def _observer_workflow() -> str:
    return """
version: 1
name: observer
work_type: dev
defaults:
  provider: fake
phases:
  - id: design
    policy: observer
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


def test_flow_start_can_prepare_empty_workspace_from_cli(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workspace", "reuse"],
    )

    store = LocalFlowStore(tmp_path / "_wt")
    run_id = store.latest_run_id("feat")
    workspace = store.read_run_json("feat", run_id, "workspace.json")
    assert result.exit_code == 0, result.output
    assert "workspace: reuse" in result.output
    assert isinstance(workspace, dict)
    assert workspace["mode"] == "reuse"
    assert (tmp_path / "_wt" / "feat" / "feat.code-workspace").is_file()


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


def test_flow_inspect_reports_workflow_structure_and_json(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())

    table = runner.invoke(app, ["flow", "inspect", "dev-complex", "--root", str(tmp_path)])
    json_result = runner.invoke(
        app,
        ["flow", "inspect", "dev-complex", "--root", str(tmp_path), "--json"],
    )

    assert table.exit_code == 0, table.output
    assert "pal flow workflow" in table.output
    assert json_result.exit_code == 0, json_result.output
    assert '"name": "dev-complex"' in json_result.output
    assert '"initial_phase": "design"' in json_result.output
    assert '"id": "designer"' in json_result.output
    assert '"policy": "co-driver"' in json_result.output


def test_flow_inspect_reports_missing_workflow(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flow", "inspect", "missing", "--root", str(tmp_path)])

    assert result.exit_code != 0
    assert "Workflow spec not found" in _plain(result.output)


def test_flow_init_lists_templates(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["flow", "init", "--root", str(tmp_path), "--list-templates"],
    )

    assert result.exit_code == 0, result.output
    assert "dev-complex" in result.output
    assert "dev-routine" in result.output
    assert "complex" in result.output


def test_flow_init_creates_valid_workflow_spec(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "flow",
            "init",
            "team-flow",
            "--root",
            str(tmp_path),
            "--provider",
            "fake",
            "--repo",
            "api",
            "--repo",
            "web",
        ],
    )

    validate = runner.invoke(app, ["flow", "validate", "team-flow", "--root", str(tmp_path)])
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "team-flow"],
    )

    assert result.exit_code == 0, result.output
    assert "pal flow initialized" in result.output
    assert "api, web" in result.output
    assert (tmp_path / ".pal" / "flows" / "team-flow.yaml").is_file()
    assert validate.exit_code == 0, validate.output
    assert start.exit_code == 0, start.output
    assert "explore" in start.output


def test_flow_init_rejects_existing_spec_unless_forced(tmp_path: Path) -> None:
    first = runner.invoke(
        app,
        ["flow", "init", "routine", "--root", str(tmp_path), "--provider", "fake"],
    )
    blocked = runner.invoke(
        app,
        ["flow", "init", "routine", "--root", str(tmp_path), "--provider", "fake"],
    )
    forced = runner.invoke(
        app,
        [
            "flow",
            "init",
            "routine",
            "--root",
            str(tmp_path),
            "--provider",
            "fake",
            "--template",
            "dev-routine",
            "--force",
        ],
    )

    assert first.exit_code == 0, first.output
    assert blocked.exit_code != 0
    assert "Workflow already exists" in _plain(blocked.output)
    assert forced.exit_code == 0, forced.output
    assert "dev-routine" in forced.output
    assert "mode: routine" in (tmp_path / ".pal" / "flows" / "routine.yaml").read_text(
        encoding="utf-8"
    )


def test_flow_init_reports_template_and_provider_errors(tmp_path: Path) -> None:
    missing_template = runner.invoke(
        app,
        [
            "flow",
            "init",
            "bad",
            "--root",
            str(tmp_path),
            "--provider",
            "fake",
            "--template",
            "missing",
        ],
    )
    missing_provider = runner.invoke(
        app,
        ["flow", "init", "bad", "--root", str(tmp_path), "--provider", "missing"],
    )

    assert missing_template.exit_code != 0
    assert "Unknown workflow template" in _plain(missing_template.output)
    assert missing_provider.exit_code != 0
    assert "Unknown provider" in _plain(missing_provider.output)


def test_flow_init_reports_generated_validation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WorkflowLibrary:
        root = tmp_path

    class InvalidResult:
        valid = False
        errors = ["broken generated spec"]

    class Service:
        workflow_library = WorkflowLibrary()

        def provider(self, _name: str) -> object:
            return object()

        def validate_workflow(self, _path: Path) -> InvalidResult:
            return InvalidResult()

    monkeypatch.setattr(flow_cli, "build_local_flow_service", lambda _cfg: Service())

    result = runner.invoke(
        app,
        ["flow", "init", "bad", "--root", str(tmp_path), "--provider", "fake"],
    )

    assert result.exit_code != 0
    assert "Generated workflow spec is invalid" in _plain(result.output)


def test_flow_render_writes_phase_brief_artifacts(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "dev-complex"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        ["flow", "render", "feat", "--root", str(tmp_path), "--provider", "fake"],
    )

    store = LocalFlowStore(tmp_path / "_wt")
    run_id = store.latest_run_id("feat")
    phase_dir = store.phase_dir("feat", run_id, "design")
    assert result.exit_code == 0, result.output
    assert "pal flow rendered" in result.output
    assert "brief.md" in result.output
    assert (phase_dir / "brief.md").is_file()
    assert (phase_dir / "brief.json").is_file()
    assert _event_types(tmp_path, "feat")[-1] == "flow.phase.rendered"


def test_flow_render_rejects_unknown_provider(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        ["flow", "render", "feat", "--root", str(tmp_path), "--provider", "missing"],
    )

    assert result.exit_code != 0
    assert "Unknown provider" in _plain(result.output)


def test_flow_execute_runs_phase_and_writes_execution_manifest(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "dev-complex"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        [
            "flow",
            "execute",
            "feat",
            "--root",
            str(tmp_path),
            "--provider",
            "fake",
            "--agent",
            "designer",
        ],
    )

    store = LocalFlowStore(tmp_path / "_wt")
    run_id = store.latest_run_id("feat")
    execution_root = store.phase_dir("feat", run_id, "design") / "executions"
    manifests = list(execution_root.glob("*/manifest.json"))
    assert result.exit_code == 0, result.output
    assert "pal flow executed" in result.output
    assert "completed" in result.output
    assert "manifest.json" in result.output
    assert len(manifests) == 1
    assert _event_types(tmp_path, "feat")[-3:] == [
        "flow.phase.rendered",
        "flow.phase.execution.started",
        "flow.phase.execution.completed",
    ]


def test_flow_execute_rejects_missing_agent(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _valid_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "dev-complex"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        ["flow", "execute", "feat", "--root", str(tmp_path), "--agent", "missing"],
    )

    assert result.exit_code != 0
    assert "no rendered agent" in _plain(result.output)


def test_flow_execute_observer_policy_can_be_forced(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "observer", _observer_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "observer"],
    )
    assert start.exit_code == 0, start.output

    blocked = runner.invoke(app, ["flow", "execute", "feat", "--root", str(tmp_path)])
    forced = runner.invoke(
        app,
        ["flow", "execute", "feat", "--root", str(tmp_path), "--force-policy"],
    )

    assert blocked.exit_code != 0
    assert "Observer policy" in _plain(blocked.output)
    assert forced.exit_code == 0, forced.output


def test_flow_ship_writes_manifest_for_feature_repos(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "_wt" / "feat" / "api")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--repo", "api"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        [
            "flow",
            "ship",
            "feat",
            "--root",
            str(tmp_path),
            "--commit",
            "--message",
            "Ship feat",
            "--push",
            "--create-pr",
            "--body",
            "ship body",
        ],
    )

    store = LocalFlowStore(tmp_path / "_wt")
    run_id = store.latest_run_id("feat")
    assert result.exit_code == 0, result.output
    assert "would_commit" in result.output
    assert "would_push" in result.output
    assert "would_create" in result.output
    assert (store.ship_dir("feat", run_id) / "manifest.json").is_file()
    assert _event_types(tmp_path, "feat")[-1] == "flow.ship.completed"


def test_flow_ship_reports_failures_and_body_file_errors(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    missing_body = runner.invoke(
        app,
        [
            "flow",
            "ship",
            "feat",
            "--root",
            str(tmp_path),
            "--body-file",
            str(tmp_path / "missing.md"),
        ],
    )
    no_repos = runner.invoke(app, ["flow", "ship", "feat", "--root", str(tmp_path)])
    missing_message = runner.invoke(
        app,
        ["flow", "ship", "feat", "--root", str(tmp_path), "--commit"],
    )

    assert missing_body.exit_code != 0
    assert "Cannot read body file" in _plain(missing_body.output)
    assert no_repos.exit_code != 0
    assert "No git repos found" in _plain(no_repos.output)
    assert missing_message.exit_code != 0
    assert "--message is required" in _plain(missing_message.output)


def test_flow_ship_exits_nonzero_when_ship_action_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path / "_wt" / "feat" / "api")
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--repo", "api"],
    )
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        [
            "flow",
            "ship",
            "feat",
            "--root",
            str(tmp_path),
            "--push",
            "--no-dry-run",
        ],
    )

    store = LocalFlowStore(tmp_path / "_wt")
    run_id = store.latest_run_id("feat")
    manifest = store.read_run_json("feat", run_id, "ship/manifest.json")
    assert result.exit_code == 1
    assert isinstance(manifest, dict)
    assert "git push failed" in manifest["repos"][0]["error"]
    assert _event_types(tmp_path, "feat")[-1] == "flow.ship.failed"


def test_flow_artifacts_command_reports_missing_and_present_artifacts(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "artifact", _artifact_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "artifact"],
    )
    assert start.exit_code == 0, start.output

    missing = runner.invoke(app, ["flow", "artifacts", "feat", "--root", str(tmp_path)])
    artifact_path = tmp_path / "_wt" / "feat" / ".pal" / "artifacts" / "design.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("ok\n", encoding="utf-8")
    present = runner.invoke(app, ["flow", "artifacts", "feat", "--root", str(tmp_path)])

    assert missing.exit_code == 1
    assert "artifacts/design.md" in missing.output
    assert present.exit_code == 0, present.output
    assert "yes" in present.output


def test_flow_artifacts_command_reports_no_required_artifacts(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    result = runner.invoke(app, ["flow", "artifacts", "feat", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "(no required artifacts)" in result.output


def test_flow_advance_enforces_artifacts_and_accepts_force(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "artifact", _artifact_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "artifact"],
    )
    assert start.exit_code == 0, start.output

    blocked = runner.invoke(app, ["flow", "advance", "feat", "--root", str(tmp_path)])
    forced = runner.invoke(
        app,
        ["flow", "advance", "feat", "--root", str(tmp_path), "--force-artifacts"],
    )

    assert blocked.exit_code != 0
    assert "missing required artifacts" in _plain(blocked.output)
    assert forced.exit_code == 0, forced.output
    assert "implement" in forced.output


def test_flow_run_command_stops_on_policy_and_can_force_artifacts(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "artifact", _artifact_workflow())
    start = runner.invoke(
        app,
        ["flow", "start", "feat", "--root", str(tmp_path), "--workflow", "artifact"],
    )
    assert start.exit_code == 0, start.output

    missing = runner.invoke(app, ["flow", "run", "feat", "--root", str(tmp_path)])
    forced = runner.invoke(
        app,
        ["flow", "run", "feat", "--root", str(tmp_path), "--force-artifacts", "--max-phases", "1"],
    )

    assert missing.exit_code == 0, missing.output
    assert "missing_artifacts" in missing.output
    assert forced.exit_code == 0, forced.output
    assert "max_phases" in forced.output


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


def test_flow_watch_follow_uses_poll_guard(tmp_path: Path) -> None:
    start = runner.invoke(app, ["flow", "start", "feat", "--root", str(tmp_path)])
    assert start.exit_code == 0, start.output

    result = runner.invoke(
        app,
        [
            "flow",
            "watch",
            "feat",
            "--root",
            str(tmp_path),
            "--follow",
            "--poll-interval",
            "0",
            "--max-polls",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "flow.run.started" in result.output


def test_flow_watch_follow_allows_empty_event_stream_with_guard(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    service = flow_cli.build_local_flow_service(flow_cli._cfg_from_ctx(tmp_path, None, None))
    run = service.start(feature="feat", repos=[], mode="routine", phase=FlowPhase.EXPLORE)
    FlowEventLog(store.events_path("feat", run.run_id)).path.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "flow",
            "watch",
            "feat",
            "--root",
            str(tmp_path),
            "--follow",
            "--poll-interval",
            "0",
            "--max-polls",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "No events recorded" in result.output


def test_flow_watch_rejects_invalid_follow_options(tmp_path: Path) -> None:
    bad_interval = runner.invoke(
        app,
        ["flow", "watch", "feat", "--root", str(tmp_path), "--poll-interval", "-1"],
    )
    bad_polls = runner.invoke(
        app,
        ["flow", "watch", "feat", "--root", str(tmp_path), "--max-polls", "-1"],
    )

    assert bad_interval.exit_code != 0
    assert "--poll-interval must be non-negative" in _plain(bad_interval.output)
    assert bad_polls.exit_code != 0
    assert "--max-polls must be non-negative" in _plain(bad_polls.output)


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
    assert flow_cli._follow_should_continue(None, 100) is True
    assert flow_cli._follow_should_continue(1, 0) is True
    assert flow_cli._follow_should_continue(1, 1) is False

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


def test_follow_events_prints_new_events() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = 0

        def events(self, _feature: str, _run_id: str | None) -> list[FlowEvent]:
            self.calls += 1
            return [
                FlowEvent(
                    id="evt_new",
                    run_id="run_1",
                    type="custom",
                    timestamp="now",
                    phase=FlowPhase.EXPLORE,
                    actor="test",
                )
            ]

    service = Service()

    flow_cli._follow_events(
        service,
        "feat",
        None,
        seen_ids=set(),
        poll_interval=0,
        max_polls=1,
    )

    assert service.calls == 1


def test_flow_cfg_helper_accepts_worktree_and_branch_overrides(tmp_path: Path) -> None:
    cfg = flow_cli._cfg_from_ctx(tmp_path, tmp_path / "custom-wt", "bug")

    assert cfg.worktree_root == tmp_path / "custom-wt"
    assert cfg.branch_prefix == "bug"
