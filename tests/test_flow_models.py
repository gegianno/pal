from __future__ import annotations

from pal.flow.models import (
    FlowEvent,
    FlowPhase,
    FlowPolicy,
    FlowRun,
    FlowStatus,
    new_id,
    utc_now,
)


def test_flow_run_round_trips_with_policy_and_pr_fields() -> None:
    run = FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=["api"],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies={"explore": FlowPolicy.AUTONOMOUS},
        artifact_root="/tmp/feat/.pal/artifacts",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:01Z",
        pr_urls=["https://example.test/pr/1"],
        workflow_name="dev-complex",
        work_type="dev",
        phase_history=[{"phase": "explore", "entered_at": "now"}],
        approvals={"explore": "approved"},
        approval_reasons={"explore": "Accepted blocked verification."},
        blocked_reason="waiting for API",
        blocked_at="later",
        request="Fix the multi-select styling.",
    )

    parsed = FlowRun.from_dict(run.to_dict())

    assert parsed == run


def test_flow_run_from_dict_accepts_missing_optional_fields() -> None:
    parsed = FlowRun.from_dict(
        {
            "run_id": "run_1",
            "feature": "feat",
            "mode": "routine",
            "repos": ["api"],
            "current_phase": "design",
            "status": "running",
            "artifact_root": "/tmp/artifacts",
            "created_at": "now",
            "updated_at": "now",
        }
    )

    assert parsed.current_phase == FlowPhase.DESIGN
    assert parsed.policies == {}
    assert parsed.pr_urls == []
    assert parsed.workflow_name == ""
    assert parsed.work_type == ""
    assert parsed.phase_history == []
    assert parsed.request == ""
    assert parsed.approvals == {}
    assert parsed.approval_reasons == {}
    assert parsed.blocked_reason == ""
    assert parsed.blocked_at == ""


def test_flow_run_from_dict_filters_non_mapping_phase_history() -> None:
    parsed = FlowRun.from_dict(
        {
            "run_id": "run_1",
            "feature": "feat",
            "mode": "routine",
            "repos": [],
            "current_phase": "implement",
            "status": "blocked",
            "policies": {},
            "artifact_root": "/tmp/artifacts",
            "created_at": "now",
            "updated_at": "now",
            "phase_history": [{"phase": "implement"}, "bad"],
            "approvals": {"design": 1},
            "approval_reasons": {"design": 2},
        }
    )

    assert parsed.status == FlowStatus.BLOCKED
    assert parsed.phase_history == [{"phase": "implement"}]
    assert parsed.approvals == {"design": "1"}
    assert parsed.approval_reasons == {"design": "2"}


def test_flow_event_round_trips_with_and_without_phase() -> None:
    event = FlowEvent(
        id="evt_1",
        run_id="run_1",
        type="flow.run.started",
        timestamp="2026-04-27T00:00:00Z",
        phase=FlowPhase.EXPLORE,
        actor="pal",
        payload={"feature": "feat"},
        causation_id="cause",
        correlation_id="run_1",
    )

    assert FlowEvent.from_dict(event.to_dict()) == event

    no_phase = FlowEvent.from_dict(
        {
            **event.to_dict(),
            "id": "evt_2",
            "phase": None,
            "payload": {},
            "causation_id": None,
            "correlation_id": None,
        }
    )
    assert no_phase.phase is None
    assert no_phase.payload == {}


def test_default_id_and_timestamp_helpers() -> None:
    assert new_id("run").startswith("run_")
    assert utc_now().endswith("Z")
