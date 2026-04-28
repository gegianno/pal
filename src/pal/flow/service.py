from __future__ import annotations

from typing import Callable

from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus, new_id, utc_now
from .providers.base import FlowProvider
from .store import LocalFlowStore


def default_policies() -> dict[str, FlowPolicy]:
    return {
        FlowPhase.EXPLORE.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.DESIGN.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.IMPLEMENT.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.VERIFY.value: FlowPolicy.SUPERVISOR,
        FlowPhase.REVIEW.value: FlowPolicy.OBSERVER,
        FlowPhase.SHIP.value: FlowPolicy.SUPERVISOR,
    }


class LocalFlowService:
    def __init__(
        self,
        store: LocalFlowStore,
        provider: FlowProvider,
        *,
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock
        self.id_factory = id_factory

    def start(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str = "routine",
        phase: FlowPhase = FlowPhase.EXPLORE,
    ) -> FlowRun:
        now = self.clock()
        run = FlowRun(
            run_id=self.id_factory("run"),
            feature=feature,
            mode=mode,
            repos=list(repos),
            current_phase=phase,
            status=FlowStatus.RUNNING,
            policies=default_policies(),
            artifact_root=str(self.store.pal_dir(feature) / "artifacts"),
            created_at=now,
            updated_at=now,
        )
        self.store.feature_dir(feature).mkdir(parents=True, exist_ok=True)
        self.store.create_run(run)
        self._append_event(
            run,
            event_type="flow.run.started",
            actor="pal",
            payload={"feature": feature, "mode": mode, "repos": list(repos)},
        )
        provider_result = self.provider.start(run)
        self._append_event(
            run,
            event_type="provider.started",
            actor=provider_result.provider,
            payload={"summary": provider_result.summary, **provider_result.payload},
        )
        return run

    def status(self, feature: str, run_id: str | None = None) -> FlowRun:
        return self.store.load_run(feature, run_id)

    def events(self, feature: str, run_id: str | None = None) -> list[FlowEvent]:
        return self.store.read_events(feature, run_id)

    def _append_event(
        self,
        run: FlowRun,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, object],
    ) -> None:
        event = FlowEvent(
            id=self.id_factory("evt"),
            run_id=run.run_id,
            type=event_type,
            timestamp=self.clock(),
            phase=run.current_phase,
            actor=actor,
            payload=payload,
            correlation_id=run.run_id,
        )
        self.store.append_event(run.feature, run.run_id, event)
