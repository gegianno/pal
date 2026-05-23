from __future__ import annotations

import pytest

from pal.flow.models import FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.policy import auto_advance_allowed, ensure_execution_allowed, phase_policy


def test_phase_policy_uses_run_policy_and_autonomous_fallback() -> None:
    run = FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=[],
        current_phase=FlowPhase.REVIEW,
        status=FlowStatus.RUNNING,
        policies={"review": FlowPolicy.OBSERVER},
        artifact_root="/tmp/artifacts",
        created_at="now",
        updated_at="now",
    )
    fallback = FlowRun.from_dict({**run.to_dict(), "current_phase": "pr", "policies": {}})

    assert phase_policy(run) == FlowPolicy.OBSERVER
    assert phase_policy(fallback) == FlowPolicy.AUTONOMOUS


def test_execution_policy_rejects_observer_unless_forced() -> None:
    with pytest.raises(ValueError, match="Observer policy"):
        ensure_execution_allowed(FlowPolicy.OBSERVER)

    ensure_execution_allowed(FlowPolicy.OBSERVER, force=True)
    ensure_execution_allowed(FlowPolicy.SUPERVISOR)


def test_auto_advance_policy_rules() -> None:
    assert auto_advance_allowed(FlowPolicy.AUTONOMOUS) is True
    assert auto_advance_allowed(FlowPolicy.CO_DRIVER) is False
    assert auto_advance_allowed(FlowPolicy.CO_DRIVER, co_driver_auto_advance=True) is True
    assert auto_advance_allowed(FlowPolicy.SUPERVISOR) is False
