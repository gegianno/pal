from __future__ import annotations

from .models import FlowPolicy, FlowRun


def phase_policy(run: FlowRun) -> FlowPolicy:
    return run.policies.get(run.current_phase.value, FlowPolicy.AUTONOMOUS)


def ensure_execution_allowed(policy: FlowPolicy, *, force: bool = False) -> None:
    if policy == FlowPolicy.OBSERVER and not force:
        raise ValueError(
            "Observer policy does not allow phase execution. Use --force-policy to override."
        )


def auto_advance_allowed(policy: FlowPolicy, *, co_driver_auto_advance: bool = False) -> bool:
    if policy == FlowPolicy.AUTONOMOUS:
        return True
    return policy == FlowPolicy.CO_DRIVER and co_driver_auto_advance
