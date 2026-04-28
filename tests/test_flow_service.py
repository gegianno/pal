from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.models import FlowPhase, FlowStatus
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.service import LocalFlowService, default_policies
from pal.flow.store import LocalFlowStore


def _ids() -> list[str]:
    return ["run_test", "evt_started", "evt_provider", "session_test", "evt_completed"]


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
