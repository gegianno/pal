from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus, new_id, utc_now
from .providers.base import FlowProvider, ProviderLaunchRequest, ProviderPreflight
from .store import LocalFlowStore
from .workflows.library import LocalWorkflowLibrary, WorkflowSpecError
from .workflows.models import WorkflowSpec, WorkflowValidationResult


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
        workflow_library: LocalWorkflowLibrary | None = None,
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self.store = store
        self.providers = dict(providers)
        self.default_provider = default_provider
        self.workflow_library = workflow_library
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

    def workflow_paths(self) -> list[Path]:
        return self.workflow_library.list_paths() if self.workflow_library else []

    def load_workflow(self, workflow: str | Path) -> WorkflowSpec:
        if not self.workflow_library:
            raise WorkflowSpecError("Workflow specs are not configured for this service.")
        return self.workflow_library.load(workflow)

    def validate_workflow(self, workflow: str | Path) -> WorkflowValidationResult:
        path = (
            self.workflow_library.resolve_path(workflow)
            if self.workflow_library
            else Path(workflow)
        )
        try:
            spec = self.load_workflow(workflow)
        except WorkflowSpecError as exc:
            return WorkflowValidationResult(path=path, name="", work_type="", errors=[str(exc)])
        return WorkflowValidationResult(
            path=spec.path,
            name=spec.name,
            work_type=spec.work_type,
            errors=self._workflow_provider_errors(spec),
        )

    def validate_workflows(self) -> list[WorkflowValidationResult]:
        return [self.validate_workflow(path) for path in self.workflow_paths()]

    def start(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str | None = None,
        phase: FlowPhase | None = None,
        provider_name: str | None = None,
        headless: bool = False,
        prompt: str = "",
        workflow: str | None = None,
    ) -> FlowRun:
        workflow_spec = self.load_workflow(workflow) if workflow else None
        if workflow_spec:
            validation = self.validate_workflow(workflow_spec.path)
            if not validation.valid:
                raise ValueError("Workflow spec is invalid: " + "; ".join(validation.errors))

        selected_provider = (
            provider_name
            or (workflow_spec.defaults.provider if workflow_spec else "")
            or self.default_provider
        )
        provider = self.provider(selected_provider)
        selected_phase = phase or (
            workflow_spec.initial_phase if workflow_spec else FlowPhase.EXPLORE
        )
        selected_mode = mode or (workflow_spec.mode if workflow_spec else "routine")
        selected_repos = (
            list(repos) if repos else (list(workflow_spec.repos) if workflow_spec else [])
        )
        policies = workflow_spec.policies_by_phase() if workflow_spec else default_policies()
        now = self.clock()
        run = FlowRun(
            run_id=self.id_factory("run"),
            feature=feature,
            mode=selected_mode,
            repos=selected_repos,
            current_phase=selected_phase,
            status=FlowStatus.RUNNING,
            policies=policies,
            artifact_root=str(self.store.pal_dir(feature) / "artifacts"),
            created_at=now,
            updated_at=now,
            workflow_name=workflow_spec.name if workflow_spec else "",
            work_type=workflow_spec.work_type if workflow_spec else "",
        )
        self.store.feature_dir(feature).mkdir(parents=True, exist_ok=True)
        self.store.create_run(run)
        if workflow_spec:
            self.store.write_run_json(run, "workflow.json", workflow_spec.to_run_dict())
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
        payload = {
            "feature": feature,
            "mode": selected_mode,
            "repos": selected_repos,
            "provider": provider.name,
            "headless": headless,
        }
        if workflow_spec:
            payload["workflow"] = workflow_spec.name
            payload["work_type"] = workflow_spec.work_type
        self._append_event(run, event_type="flow.run.started", actor="pal", payload=payload)
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

    def _workflow_provider_errors(self, spec: WorkflowSpec) -> list[str]:
        errors: list[str] = []
        for provider_name in sorted(spec.referenced_providers(self.default_provider)):
            if provider_name not in self.providers:
                errors.append(f"Unknown provider '{provider_name}' in workflow '{spec.name}'.")

        for phase in spec.phases:
            for agent in phase.agents:
                provider_name = (
                    agent.provider
                    or phase.provider
                    or spec.defaults.provider
                    or self.default_provider
                )
                provider = self.providers.get(provider_name)
                if not provider:
                    continue
                capabilities = provider.capabilities()
                for requirement in agent.requires:
                    if requirement in capabilities.execution_modes:
                        continue
                    if hasattr(capabilities, requirement):
                        supported = bool(getattr(capabilities, requirement))
                        if supported:
                            continue
                        errors.append(
                            f"Agent '{agent.id}' requires '{requirement}', but provider "
                            f"'{provider_name}' does not support it."
                        )
                        continue
                    errors.append(f"Agent '{agent.id}' has unknown requirement '{requirement}'.")
        return errors

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
