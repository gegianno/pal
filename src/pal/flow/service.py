from __future__ import annotations

from typing import Callable, Mapping

from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus, new_id, utc_now
from .providers.base import FlowProvider, ProviderLaunchRequest, ProviderPreflight
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
        providers: Mapping[str, FlowProvider],
        *,
        default_provider: str = "fake",
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self.store = store
        self.providers = dict(providers)
        self.default_provider = default_provider
        self.clock = clock
        self.id_factory = id_factory

    def provider_names(self) -> list[str]:
        return sorted(self.providers)

    def provider(self, name: str | None = None) -> FlowProvider:
        provider_name = name or self.default_provider
        try:
            return self.providers[provider_name]
        except KeyError as exc:
            valid = ", ".join(self.provider_names())
            raise ValueError(
                f"Unknown provider '{provider_name}'. Expected one of: {valid}"
            ) from exc

    def preflight_provider(self, name: str) -> ProviderPreflight:
        return self.provider(name).preflight()

    def preflight_all(self) -> list[ProviderPreflight]:
        return [self.providers[name].preflight() for name in self.provider_names()]

    def start(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str = "routine",
        phase: FlowPhase = FlowPhase.EXPLORE,
        provider_name: str | None = None,
        headless: bool = False,
        prompt: str = "",
    ) -> FlowRun:
        provider = self.provider(provider_name)
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
        preflight = provider.preflight()
        self.store.write_run_json(
            run,
            "provider-capabilities.json",
            {provider.name: preflight.capabilities.to_dict()},
        )
        self.store.write_run_json(
            run,
            "provider-auth.json",
            {provider.name: preflight.auth.to_dict()},
        )
        self._append_event(
            run,
            event_type="flow.run.started",
            actor="pal",
            payload={
                "feature": feature,
                "mode": mode,
                "repos": list(repos),
                "provider": provider.name,
                "headless": headless,
            },
        )
        provider_result = provider.start(run)
        self._append_event(
            run,
            event_type="provider.started",
            actor=provider_result.provider,
            payload={"summary": provider_result.summary, **provider_result.payload},
        )
        if headless:
            launch_started_at = self.clock()
            launch_result = provider.launch_headless(
                ProviderLaunchRequest(
                    run=run,
                    workspace_dir=self.store.feature_dir(feature),
                    prompt=prompt,
                    output_dir=self.store.latest_output_dir(feature, run.run_id),
                )
            )
            launch_ended_at = self.clock()
            output_paths = self.store.write_latest_output(
                run,
                provider=provider.name,
                stdout=launch_result.stdout,
                stderr=launch_result.stderr,
            )
            self.store.write_run_json(
                run,
                "sessions.json",
                [
                    {
                        **launch_result.to_session_dict(
                            session_id=self.id_factory("session"),
                            started_at=launch_started_at,
                            ended_at=launch_ended_at,
                        ),
                        "stdout_log": output_paths["stdout"],
                        "stderr_log": output_paths["stderr"],
                        "provider_log": output_paths["provider_log"],
                    }
                ],
            )
            self._append_event(
                run,
                event_type="provider.completed",
                actor=provider.name,
                payload={
                    "status": launch_result.status,
                    "returncode": launch_result.returncode,
                    "stdout_log": output_paths["stdout"],
                    "stderr_log": output_paths["stderr"],
                    "provider_log": output_paths["provider_log"],
                },
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
