from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping

from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus, new_id, utc_now
from .providers.base import FlowProvider, ProviderLaunchRequest, ProviderPreflight
from .rendering import PhaseBrief, build_phase_brief
from .store import LocalFlowStore
from .workflows.library import LocalWorkflowLibrary, WorkflowSpecError
from .workflows.models import WorkflowSpec, WorkflowValidationResult

_DEFAULT_PHASE_ORDER = [
    FlowPhase.EXPLORE,
    FlowPhase.DESIGN,
    FlowPhase.IMPLEMENT,
    FlowPhase.VERIFY,
    FlowPhase.REVIEW,
    FlowPhase.SHIP,
]


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
            phase_history=[
                {
                    "phase": selected_phase.value,
                    "entered_at": now,
                    "actor": "pal",
                    "reason": "start",
                }
            ],
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

    def render_phase(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        provider_name: str = "",
    ) -> PhaseBrief:
        if provider_name:
            self.provider(provider_name)
        run = self.status(feature, run_id)
        workflow = self._stored_workflow(run)
        events = self.events(feature, run.run_id)
        brief = build_phase_brief(
            run=run,
            workflow=workflow,
            events=events,
            rendered_at=self.clock(),
            default_provider=self.default_provider,
            provider_filter=provider_name,
        )
        paths = self.store.write_phase_brief(
            run,
            phase=run.current_phase.value,
            markdown=brief.to_markdown(),
            data=brief.to_dict(),
        )
        rendered = brief.with_paths(paths)
        self._append_event(
            run,
            event_type="flow.phase.rendered",
            actor="pal",
            payload={
                "phase": run.current_phase.value,
                "markdown": paths["markdown"],
                "json": paths["json"],
                "providers": sorted(rendered.provider_guidance),
            },
        )
        return rendered

    def approve(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        phase: FlowPhase | None = None,
        actor: str = "human",
    ) -> FlowRun:
        run = self.status(feature, run_id)
        workflow = self._stored_workflow(run)
        target_phase = phase or run.current_phase
        if not self._phase_requires_approval(workflow, target_phase):
            raise ValueError(f"Phase '{target_phase.value}' does not require approval.")
        now = self.clock()
        updated = replace(
            run,
            approvals={**run.approvals, target_phase.value: now},
            updated_at=now,
        )
        self.store.save_state(updated)
        self._append_event(
            updated,
            event_type="flow.phase.approved",
            actor=actor,
            payload={"phase": target_phase.value, "approved_at": now},
        )
        return updated

    def advance(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        signal: str = "complete",
        actor: str = "pal",
    ) -> FlowRun:
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run)
        workflow = self._stored_workflow(run)
        signal = signal.strip() or "complete"
        if signal == "complete" and self._approval_required_but_missing(run, workflow):
            raise ValueError(f"Phase '{run.current_phase.value}' requires approval before advance.")

        target = self._transition_target(run, workflow, signal)
        now = self.clock()
        if target is None:
            updated = replace(
                run,
                status=FlowStatus.COMPLETED,
                updated_at=now,
                blocked_reason="",
                blocked_at="",
                phase_history=[
                    *run.phase_history,
                    {
                        "from": run.current_phase.value,
                        "to": "",
                        "on": signal,
                        "at": now,
                        "actor": actor,
                    },
                ],
            )
            self.store.save_state(updated)
            self._append_event(
                updated,
                event_type="flow.run.completed",
                actor=actor,
                payload={"phase": run.current_phase.value, "on": signal},
            )
            return updated

        updated = replace(
            run,
            current_phase=target,
            status=FlowStatus.RUNNING,
            updated_at=now,
            blocked_reason="",
            blocked_at="",
            phase_history=[
                *run.phase_history,
                {
                    "from": run.current_phase.value,
                    "to": target.value,
                    "on": signal,
                    "at": now,
                    "actor": actor,
                },
            ],
        )
        self.store.save_state(updated)
        self._append_event(
            updated,
            event_type="flow.phase.advanced",
            actor=actor,
            payload={"from": run.current_phase.value, "to": target.value, "on": signal},
        )
        return updated

    def block(
        self,
        feature: str,
        *,
        reason: str,
        run_id: str | None = None,
        actor: str = "pal",
    ) -> FlowRun:
        reason = reason.strip()
        if not reason:
            raise ValueError("Block reason is required.")
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run, allow_blocked=True)
        now = self.clock()
        updated = replace(
            run,
            status=FlowStatus.BLOCKED,
            blocked_reason=reason,
            blocked_at=now,
            updated_at=now,
        )
        self.store.save_state(updated)
        self._append_event(
            updated,
            event_type="flow.phase.blocked",
            actor=actor,
            payload={"phase": run.current_phase.value, "reason": reason, "blocked_at": now},
        )
        return updated

    def replan(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        phase: FlowPhase | None = None,
        reason: str = "",
        actor: str = "pal",
    ) -> FlowRun:
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run, allow_blocked=True)
        workflow = self._stored_workflow(run)
        target = phase or self._replan_target(run, workflow)
        now = self.clock()
        history = list(run.phase_history)
        if target != run.current_phase:
            history.append(
                {
                    "from": run.current_phase.value,
                    "to": target.value,
                    "on": "replan",
                    "at": now,
                    "actor": actor,
                }
            )
        updated = replace(
            run,
            current_phase=target,
            status=FlowStatus.RUNNING,
            blocked_reason="",
            blocked_at="",
            updated_at=now,
            phase_history=history,
        )
        self.store.save_state(updated)
        self._append_event(
            updated,
            event_type="flow.phase.replanned",
            actor=actor,
            payload={
                "from": run.current_phase.value,
                "to": target.value,
                "reason": reason.strip(),
            },
        )
        return updated

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

    def _stored_workflow(self, run: FlowRun) -> WorkflowSpec | None:
        if not run.workflow_name:
            return None
        data = self.store.read_run_json(run.feature, run.run_id, "workflow.json")
        if not isinstance(data, dict):
            raise ValueError(f"Stored workflow metadata is invalid for run '{run.run_id}'.")
        return WorkflowSpec.from_run_dict(data)

    def _phase_requires_approval(
        self,
        workflow: WorkflowSpec | None,
        phase: FlowPhase,
    ) -> bool:
        if not workflow:
            return False
        return workflow.phase(phase).requires_approval

    def _approval_required_but_missing(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> bool:
        return (
            self._phase_requires_approval(workflow, run.current_phase)
            and run.current_phase.value not in run.approvals
        )

    def _transition_target(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
        signal: str,
    ) -> FlowPhase | None:
        if workflow:
            target = workflow.transition_target(run.current_phase, signal)
            if target or signal == "complete":
                return target
            raise ValueError(f"Phase '{run.current_phase.value}' has no transition for '{signal}'.")
        if signal != "complete":
            raise ValueError(f"Run '{run.run_id}' has no workflow transition for '{signal}'.")
        return self._next_default_phase(run.current_phase)

    def _next_default_phase(self, phase: FlowPhase) -> FlowPhase | None:
        index = _DEFAULT_PHASE_ORDER.index(phase)
        next_index = index + 1
        return _DEFAULT_PHASE_ORDER[next_index] if next_index < len(_DEFAULT_PHASE_ORDER) else None

    def _replan_target(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> FlowPhase:
        if workflow:
            target = workflow.transition_target(run.current_phase, "blocked")
            if target:
                return target
            phase_ids = [phase.id for phase in workflow.phases]
            return FlowPhase.DESIGN if FlowPhase.DESIGN in phase_ids else run.current_phase
        return FlowPhase.DESIGN if run.current_phase != FlowPhase.DESIGN else run.current_phase

    def _ensure_can_mutate(self, run: FlowRun, *, allow_blocked: bool = False) -> None:
        if run.status == FlowStatus.BLOCKED and allow_blocked:
            return
        if run.status != FlowStatus.RUNNING:
            raise ValueError(f"Run '{run.run_id}' is {run.status.value} and cannot be changed.")

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
