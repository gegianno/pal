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
) -> str:
    agent_provider_line = f"        provider: {agent_provider}\n" if agent_provider else ""
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
    agents:
      - id: designer
        role: design
{agent_provider_line}        requires:
          - {requirement}
  - id: implement
    agents: []
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
