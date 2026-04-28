from __future__ import annotations

from pathlib import Path

from pal.flow.models import FlowPhase, FlowStatus
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.service import LocalFlowService, default_policies
from pal.flow.store import LocalFlowStore


def _ids() -> list[str]:
    return ["run_test", "evt_started", "evt_provider"]


def test_default_policies_match_routine_flow_defaults() -> None:
    policies = default_policies()

    assert policies["explore"].value == "autonomous"
    assert policies["verify"].value == "supervisor"
    assert policies["review"].value == "observer"


def test_flow_service_start_persists_run_and_provider_events(tmp_path: Path) -> None:
    ids = _ids()
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        provider=FakeFlowProvider(),
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
    assert events[0].payload == {"feature": "feat", "mode": "complex", "repos": ["api", "web"]}
    assert events[1].actor == "fake"
    assert events[1].payload["summary"] == "fake provider initialized run run_test"
    assert service.events("feat", "run_test") == events
