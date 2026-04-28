from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.models import FlowPhase, FlowStatus
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.service import LocalFlowService, default_policies
from pal.flow.store import LocalFlowStore
from pal.flow.workflows.library import LocalWorkflowLibrary, WorkflowSpecError


def _ids() -> list[str]:
    return ["run_test", "evt_started", "evt_provider", "session_test", "evt_completed"]


def _write_workflow(root: Path, name: str, body: str) -> Path:
    path = root / ".pal" / "flows" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _workflow_body(
    *,
    provider: str = "fake",
    requirement: str = "local_headless",
    agent_provider: str = "",
    design_required_artifact: bool = False,
    design_requires_approval: bool = False,
    implement_blocked_transition: bool = False,
) -> str:
    agent_provider_line = f"        provider: {agent_provider}\n" if agent_provider else ""
    required_artifact_lines = (
        "    required_artifacts:\n      - artifacts/design.md\n" if design_required_artifact else ""
    )
    approval_line = "    requires_approval: true\n" if design_requires_approval else ""
    transition_lines = (
        "    transitions:\n      - on: blocked\n        to: design\n"
        if implement_blocked_transition
        else ""
    )
    return f"""
version: 1
name: dev-complex
work_type: dev
mode: complex
repos:
  - api
defaults:
  provider: {provider}
phases:
  - id: design
    policy: co-driver
{required_artifact_lines}{approval_line}    transitions:
      - on: complete
        to: implement
    agents:
      - id: designer
        role: design
{agent_provider_line}        requires:
          - {requirement}
  - id: implement
{transition_lines}    agents: []
"""


def test_default_policies_match_routine_flow_defaults() -> None:
    policies = default_policies()

    assert policies["explore"].value == "autonomous"
    assert policies["verify"].value == "supervisor"
    assert policies["review"].value == "observer"


def test_flow_service_start_persists_run_and_provider_events(tmp_path: Path) -> None:
    ids = _ids()
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )

    run = service.start(
        feature="feat",
        repos=["api", "web"],
        mode="complex",
        phase=FlowPhase.DESIGN,
    )

    assert run.run_id == "run_test"
    assert run.status == FlowStatus.RUNNING
    assert run.current_phase == FlowPhase.DESIGN
    assert run.artifact_root == str(tmp_path / "_wt" / "feat" / ".pal" / "artifacts")
    assert service.status("feat") == run
    assert service.status("feat", "run_test") == run

    events = service.events("feat")
    assert [event.type for event in events] == ["flow.run.started", "provider.started"]
    assert events[0].payload == {
        "feature": "feat",
        "mode": "complex",
        "repos": ["api", "web"],
        "provider": "fake",
        "headless": False,
    }
    assert events[1].actor == "fake"
    assert events[1].payload["summary"] == "fake provider initialized run run_test"
    assert service.events("feat", "run_test") == events


def test_flow_service_provider_registry_and_preflight(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )

    assert service.provider_names() == ["fake"]
    assert isinstance(service.provider(), FakeFlowProvider)
    assert service.preflight_provider("fake").auth.status == "available"
    assert [p.provider for p in service.preflight_all()] == ["fake"]
    with pytest.raises(ValueError, match="Unknown provider"):
        service.provider("missing")


def test_flow_service_headless_launch_records_session_and_logs(tmp_path: Path) -> None:
    ids = _ids()
    store = LocalFlowStore(tmp_path / "_wt")
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )

    run = service.start(
        feature="feat",
        repos=[],
        provider_name="fake",
        headless=True,
        prompt="Summarize this workspace",
    )

    events = service.events("feat")
    assert [event.type for event in events] == [
        "flow.run.started",
        "provider.started",
        "provider.completed",
    ]
    assert events[-1].payload["status"] == "completed"
    assert (
        Path(events[-1].payload["stdout_log"])
        .read_text(encoding="utf-8")
        .startswith("fake provider completed")
    )
    sessions = store.read_run_json("feat", run.run_id, "sessions.json")
    assert isinstance(sessions, list)
    assert sessions[0]["session_id"] == "session_test"
    assert sessions[0]["provider"] == "fake"
    assert (
        store.read_run_json("feat", run.run_id, "provider-auth.json")["fake"]["status"]
        == "available"
    )
    assert store.read_run_json("feat", run.run_id, "provider-capabilities.json")["fake"][
        "execution_modes"
    ] == ["local_headless"]


def test_flow_service_workflow_paths_and_missing_library(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )

    assert service.workflow_paths() == []
    result = service.validate_workflow("missing")
    assert result.valid is False
    assert "not configured" in result.errors[0]
    with pytest.raises(WorkflowSpecError, match="not configured"):
        service.load_workflow("missing")


def test_flow_service_validates_workflow_provider_references(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body(provider="missing"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("dev-complex")

    assert result.valid is False
    assert result.name == "dev-complex"
    assert result.work_type == "dev"
    assert "Unknown provider 'missing'" in result.errors[0]
    assert service.validate_workflows() == [result]


def test_flow_service_validates_agent_capability_requirements(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "capabilities",
        _workflow_body(provider="fake", requirement="hooks", agent_provider="fake"),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("capabilities")

    assert result.valid is False
    assert "requires 'hooks'" in result.errors[0]


def test_flow_service_accepts_supported_agent_capability_flags(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "json-output", _workflow_body(requirement="json_output"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("json-output")

    assert result.valid is True


def test_flow_service_validates_unknown_agent_requirement(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "unknown-req", _workflow_body(requirement="quantum_gpu"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("unknown-req")

    assert result.valid is False
    assert "unknown requirement 'quantum_gpu'" in result.errors[0]


def test_flow_service_start_with_workflow_persists_metadata(tmp_path: Path) -> None:
    ids = _ids()
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )

    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    assert run.workflow_name == "dev-complex"
    assert run.work_type == "dev"
    assert run.mode == "complex"
    assert run.repos == ["api"]
    assert run.current_phase == FlowPhase.DESIGN
    assert set(run.policies) == {"design", "implement"}
    assert run.policies["design"].value == "co-driver"
    assert run.policies["implement"].value == "autonomous"
    assert store.read_run_json("feat", run.run_id, "workflow.json")["name"] == "dev-complex"
    assert service.events("feat")[0].payload["workflow"] == "dev-complex"


def test_flow_service_start_allows_cli_overrides_for_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    run = service.start(
        feature="feat",
        repos=["web"],
        mode="routine",
        phase=FlowPhase.IMPLEMENT,
        provider_name="fake",
        workflow="dev-complex",
    )

    assert run.mode == "routine"
    assert run.repos == ["web"]
    assert run.current_phase == FlowPhase.IMPLEMENT


def test_flow_service_start_rejects_invalid_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "bad", _workflow_body(provider="missing"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    with pytest.raises(ValueError, match="Workflow spec is invalid"):
        service.start(feature="feat", repos=[], workflow="bad")


def test_flow_service_render_phase_writes_brief_artifacts_and_event(tmp_path: Path) -> None:
    ids = _ids()
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_required_artifact=True, design_requires_approval=True),
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    brief = service.render_phase("feat")

    assert brief.paths == {
        "markdown": str(store.phase_dir("feat", run.run_id, "design") / "brief.md"),
        "json": str(store.phase_dir("feat", run.run_id, "design") / "brief.json"),
    }
    assert (
        Path(brief.paths["markdown"])
        .read_text(encoding="utf-8")
        .startswith("# pal flow phase brief")
    )
    data = store.read_run_json("feat", run.run_id, "phase/design/brief.json")
    assert isinstance(data, dict)
    assert data["phase"]["required_artifacts"] == ["artifacts/design.md"]
    assert data["phase"]["requires_approval"] is True
    assert data["paths"] == brief.paths
    assert service.events("feat")[-1].type == "flow.phase.rendered"
    assert service.events("feat")[-1].payload["providers"] == ["fake"]


def test_flow_service_render_phase_filters_provider_and_rejects_unknown_provider(
    tmp_path: Path,
) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    brief = service.render_phase("feat", provider_name="fake")

    assert list(brief.provider_guidance) == ["fake"]
    with pytest.raises(ValueError, match="Unknown provider"):
        service.render_phase("feat", provider_name="missing")


def test_flow_service_advances_default_phase_order_and_completes(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    service.start(feature="feat", repos=[], phase=FlowPhase.REVIEW)

    advanced = service.advance("feat")
    completed = service.advance("feat")

    assert advanced.current_phase == FlowPhase.SHIP
    assert advanced.status == FlowStatus.RUNNING
    assert advanced.phase_history[-1]["from"] == "review"
    assert advanced.phase_history[-1]["to"] == "ship"
    assert completed.status == FlowStatus.COMPLETED
    assert completed.current_phase == FlowPhase.SHIP
    assert completed.phase_history[-1]["to"] == ""
    assert [event.type for event in service.events("feat")][-2:] == [
        "flow.phase.advanced",
        "flow.run.completed",
    ]
    with pytest.raises(ValueError, match="completed"):
        service.advance("feat")


def test_flow_service_rejects_unknown_transition_without_workflow(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    with pytest.raises(ValueError, match="no workflow transition"):
        service.advance("feat", signal="blocked")


def test_flow_service_enforces_approval_before_workflow_advance(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="requires approval"):
        service.advance("feat")
    approved = service.approve("feat")
    advanced = service.advance("feat")

    assert run.current_phase == FlowPhase.DESIGN
    assert approved.approvals == {"design": "2026-04-27T00:00:00Z"}
    assert advanced.current_phase == FlowPhase.IMPLEMENT
    assert advanced.status == FlowStatus.RUNNING
    assert service.events("feat")[-2].type == "flow.phase.approved"
    assert service.events("feat")[-1].type == "flow.phase.advanced"


def test_flow_service_approve_rejects_non_gated_phase(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="does not require approval"):
        service.approve("feat")


def test_flow_service_workflow_transition_signal_and_missing_signal(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="no transition"):
        service.advance("feat", signal="unknown")
    run = service.advance("feat", signal="complete")

    assert run.current_phase == FlowPhase.IMPLEMENT


def test_flow_service_block_and_replan_with_workflow_transition(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(implement_blocked_transition=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")
    service.advance("feat")

    with pytest.raises(ValueError, match="reason is required"):
        service.block("feat", reason=" ")
    blocked = service.block("feat", reason="tests failed")
    with pytest.raises(ValueError, match="blocked"):
        service.advance("feat")
    replanned = service.replan("feat", reason="need simpler design")

    assert blocked.status == FlowStatus.BLOCKED
    assert blocked.blocked_reason == "tests failed"
    assert replanned.status == FlowStatus.RUNNING
    assert replanned.current_phase == FlowPhase.DESIGN
    assert replanned.blocked_reason == ""
    assert replanned.phase_history[-1]["on"] == "replan"
    assert [event.type for event in service.events("feat")][-2:] == [
        "flow.phase.blocked",
        "flow.phase.replanned",
    ]


def test_flow_service_replan_defaults_and_explicit_phase_without_workflow(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[], phase=FlowPhase.IMPLEMENT)

    blocked = service.block("feat", reason="blocked")
    replanned = service.replan("feat")
    explicit = service.replan("feat", phase=FlowPhase.VERIFY, reason="check")

    assert blocked.status == FlowStatus.BLOCKED
    assert replanned.current_phase == FlowPhase.DESIGN
    assert explicit.current_phase == FlowPhase.VERIFY


def test_flow_service_replan_stays_on_current_phase_when_no_design_phase(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "verify-only",
        """
version: 1
name: verify-only
work_type: dev
defaults:
  provider: fake
phases:
  - id: verify
    agents: []
""",
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="verify-only")
    service.block("feat", reason="blocked")

    run = service.replan("feat")

    assert run.current_phase == FlowPhase.VERIFY
    assert run.phase_history[-1]["reason"] == "start"


def test_flow_service_rejects_invalid_stored_workflow_metadata(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")
    store.write_run_json(run, "workflow.json", [])

    with pytest.raises(ValueError, match="Stored workflow metadata is invalid"):
        service.approve("feat")
