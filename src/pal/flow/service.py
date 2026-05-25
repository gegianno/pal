from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Callable, Mapping

from ..git import git_metadata_dirs
from ..identifiers import normalize_feature_name, normalize_repo_name, safe_child_path
from ..workspaces import (
    STATE_ONLY_WORKSPACE_MODE,
    WorkspaceBackend,
    validate_workspace_mode,
)
from .artifacts import (
    ArtifactPathError,
    ArtifactValidation,
    VerificationOutcome,
    VerificationStatus,
    VerificationStatusError,
    resolve_artifact_path,
    validate_artifact_reference,
    validate_required_artifacts,
    is_verification_artifact,
    read_verification_outcome,
)
from .execution import (
    PhaseExecutionRecord,
    PhaseExecutionSummary,
    build_execution_prompt,
    execution_status,
    phase_execution_targets,
    redact_command_prompt,
)
from .evidence import (
    EvidenceRequirement,
    EvidenceStatus,
    FlowEvidence,
    FlowReadiness,
    normalize_evidence_check,
)
from .hooks import FlowHookDispatcher
from .loop import FlowRunLoopSummary, FlowRunStep
from .models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus, new_id, utc_now
from .policy import auto_advance_allowed, ensure_execution_allowed, phase_policy
from .providers.base import FlowProvider, ProviderLaunchRequest, ProviderPreflight
from .rendering import PhaseBrief, build_phase_brief
from .pr import FlowPrManager, FlowPrSummary
from .store import LocalFlowStore
from .workflows.library import LocalWorkflowLibrary, WorkflowSpecError
from .workflows.models import WorkflowSpec, WorkflowValidationResult

_DEFAULT_PHASE_ORDER = [
    FlowPhase.EXPLORE,
    FlowPhase.DESIGN,
    FlowPhase.IMPLEMENT,
    FlowPhase.VERIFY,
    FlowPhase.PR,
    FlowPhase.REVIEW,
]

_DELEGATED_TOOLS = {
    "browser",
    "github_read",
    "github_write",
    "linear_read",
    "linear_write",
    "slack_read",
    "slack_write",
}


def default_policies() -> dict[str, FlowPolicy]:
    return {
        FlowPhase.EXPLORE.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.DESIGN.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.IMPLEMENT.value: FlowPolicy.AUTONOMOUS,
        FlowPhase.VERIFY.value: FlowPolicy.SUPERVISOR,
        FlowPhase.PR.value: FlowPolicy.SUPERVISOR,
        FlowPhase.REVIEW.value: FlowPolicy.SUPERVISOR,
    }


class LocalFlowService:
    def __init__(
        self,
        store: LocalFlowStore,
        providers: Mapping[str, FlowProvider],
        *,
        default_provider: str = "fake",
        workflow_library: LocalWorkflowLibrary | None = None,
        workspace_backend: WorkspaceBackend | None = None,
        hooks: FlowHookDispatcher | None = None,
        pr_manager: FlowPrManager | None = None,
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[str], str] = new_id,
        pal_command: str = "pal",
    ) -> None:
        self.store = store
        self.providers = dict(providers)
        self.default_provider = default_provider
        self.workflow_library = workflow_library
        self.workspace_backend = workspace_backend
        self.hooks = hooks or FlowHookDispatcher()
        self.pr_manager = pr_manager or FlowPrManager()
        self.clock = clock
        self.id_factory = id_factory
        self.pal_command = pal_command.strip() or "pal"

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
            errors=[*self._workflow_provider_errors(spec), *self._workflow_artifact_errors(spec)],
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
        request: str = "",
        workflow: str | None = None,
        workspace_mode: str = STATE_ONLY_WORKSPACE_MODE,
        copy_local: bool | None = None,
        overwrite_local: bool | None = None,
    ) -> FlowRun:
        feature_name = normalize_feature_name(feature)
        normalized_workspace_mode = validate_workspace_mode(workspace_mode)
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
        self._ensure_workflow_phase(workflow_spec, selected_phase)
        selected_mode = mode or (workflow_spec.mode if workflow_spec else "routine")
        selected_repos = (
            self._normalize_repos(repos)
            if repos
            else self._normalize_repos(workflow_spec.repos if workflow_spec else [])
        )
        normalized_request = request.strip()
        policies = workflow_spec.policies_by_phase() if workflow_spec else default_policies()
        workspace_result = None
        if normalized_workspace_mode != STATE_ONLY_WORKSPACE_MODE:
            if not self.workspace_backend:
                raise ValueError("Workspace backend is not configured for this flow service.")
            workspace_result = self.workspace_backend.prepare(
                feature=feature_name,
                repos=selected_repos,
                mode=normalized_workspace_mode,
                copy_local=copy_local,
                overwrite_local=overwrite_local,
            )
        now = self.clock()
        run = FlowRun(
            run_id=self.id_factory("run"),
            feature=feature_name,
            mode=selected_mode,
            repos=selected_repos,
            current_phase=selected_phase,
            status=FlowStatus.RUNNING,
            policies=policies,
            artifact_root=str(self.store.pal_dir(feature_name) / "artifacts"),
            created_at=now,
            updated_at=now,
            workflow_name=workflow_spec.name if workflow_spec else "",
            work_type=workflow_spec.work_type if workflow_spec else "",
            request=normalized_request,
            phase_history=[
                {
                    "phase": selected_phase.value,
                    "entered_at": now,
                    "actor": "pal",
                    "reason": "start",
                }
            ],
        )
        self.store.feature_dir(feature_name).mkdir(parents=True, exist_ok=True)
        self.store.create_run(run)
        if workflow_spec:
            self.store.write_run_json(run, "workflow.json", workflow_spec.to_run_dict())
        if normalized_request:
            self.store.write_run_json(run, "request.json", {"request": normalized_request})
        if workspace_result:
            self.store.write_run_json(run, "workspace.json", workspace_result.to_dict())
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
            "feature": feature_name,
            "mode": selected_mode,
            "repos": selected_repos,
            "provider": provider.name,
            "headless": headless,
        }
        if workflow_spec:
            payload["workflow"] = workflow_spec.name
            payload["work_type"] = workflow_spec.work_type
        if normalized_request:
            payload["request_chars"] = len(normalized_request)
        self._append_event(run, event_type="flow.run.started", actor="pal", payload=payload)
        if workspace_result:
            self._append_event(
                run,
                event_type="flow.workspace.prepared",
                actor="pal",
                payload=workspace_result.to_dict(),
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
                    workspace_dir=self.store.feature_dir(feature_name),
                    prompt=prompt,
                    output_dir=self.store.latest_output_dir(feature_name, run.run_id),
                    writable_dirs=self._provider_writable_dirs(run),
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
                            command=redact_command_prompt(launch_result.command, prompt),
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

    def hook_results(self, feature: str, run_id: str | None = None) -> list[object]:
        return self.store.read_hook_results(feature, run_id)

    def evidence(self, feature: str, run_id: str | None = None) -> list[FlowEvidence]:
        return self.store.read_evidence(feature, run_id)

    def add_evidence(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        phase: FlowPhase = FlowPhase.VERIFY,
        check: str = "verification",
        status: str = EvidenceStatus.PASSED.value,
        summary: str,
        details: str = "",
        url: str = "",
        artifact: str = "",
        actor: str = "human",
    ) -> FlowEvidence:
        run = self.status(feature, run_id)
        workflow = self._stored_workflow(run)
        self._ensure_workflow_phase(workflow, phase)
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("Evidence summary is required.")
        try:
            evidence_status = EvidenceStatus(status.strip())
        except ValueError as exc:
            valid = ", ".join(item.value for item in EvidenceStatus)
            raise ValueError(f"Evidence status must be one of: {valid}.") from exc
        normalized_artifact = artifact.strip()
        if normalized_artifact:
            artifact_path = resolve_artifact_path(
                run,
                self.store.feature_dir(run.feature),
                normalized_artifact,
            )
            if not artifact_path.is_file():
                raise ValueError(f"Evidence artifact is missing: {normalized_artifact}")
        evidence = FlowEvidence(
            evidence_id=self.id_factory("evidence"),
            run_id=run.run_id,
            phase=phase,
            check=normalize_evidence_check(check),
            status=evidence_status,
            summary=normalized_summary,
            details=details.strip(),
            url=url.strip(),
            artifact=normalized_artifact,
            actor=actor.strip() or "human",
            created_at=self.clock(),
        )
        self.store.append_evidence(run, evidence)
        self._append_event(
            run,
            event_type="flow.evidence.added",
            actor=evidence.actor,
            payload=evidence.to_dict(),
        )
        return evidence

    def readiness(self, feature: str, run_id: str | None = None) -> FlowReadiness:
        run = self.status(feature, run_id)
        summary = self._readiness_summary(run)
        self._append_event(
            run,
            event_type=f"flow.readiness.{summary.status}",
            actor="pal",
            payload=summary.to_dict(),
        )
        return summary

    def pr(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        repos: list[str] | None = None,
        base: str = "main",
        title: str = "",
        body: str = "",
        dry_run: bool = True,
        commit: bool = False,
        commit_message: str = "",
        push: bool = False,
        create_pr: bool = False,
        draft: bool = False,
    ) -> FlowPrSummary:
        run = self.status(feature, run_id)
        if commit and not commit_message.strip():
            raise ValueError("--message is required when --commit is set.")
        readiness = self._readiness_summary(run)
        safe_draft = draft or (create_pr and not readiness.ready)
        normalized_title = title.strip() or f"{run.feature}: {run.workflow_name or run.mode}"
        normalized_body = body.strip() or self._default_pr_body(run, readiness)
        body_path = self.store.write_pr_body(run, normalized_body + "\n")
        summary = self.pr_manager.prepare(
            feature=run.feature,
            run_id=run.run_id,
            feature_dir=self.store.feature_dir(run.feature),
            repos=list(repos or run.repos),
            base=base.strip() or "main",
            title=normalized_title,
            body_file=Path(body_path),
            dry_run=dry_run,
            commit=commit,
            commit_message=commit_message.strip(),
            push=push,
            create_pr=create_pr,
            draft=safe_draft,
        )
        summary = summary.with_readiness(readiness.to_dict())
        manifest_path = self.store.pr_dir(run.feature, run.run_id) / "manifest.json"
        summary = summary.with_paths({"body": body_path, "manifest": str(manifest_path)})
        artifact_path = self._write_pr_phase_artifact(run, summary)
        summary = summary.with_paths({"artifact": str(artifact_path)})
        self.store.write_pr_manifest(run, summary.to_dict())
        self._append_event(
            run,
            event_type=f"flow.pr.{summary.status}",
            actor="pal",
            payload=summary.to_dict(),
        )
        return summary

    def check_artifacts(
        self,
        feature: str,
        *,
        run_id: str | None = None,
    ) -> ArtifactValidation:
        run = self.status(feature, run_id)
        workflow = self._stored_workflow(run)
        validation = self._artifact_validation(run, workflow)
        self._append_event(
            run,
            event_type="flow.artifacts.checked",
            actor="pal",
            payload=validation.to_dict(),
        )
        return validation

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
            pal_command=self.pal_command,
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

    def execute_phase(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        provider_name: str = "",
        agent_id: str = "",
        force_policy: bool = False,
    ) -> PhaseExecutionSummary:
        if provider_name:
            self.provider(provider_name)
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run)
        policy = phase_policy(run)
        ensure_execution_allowed(policy, force=force_policy)
        brief = self.render_phase(
            feature,
            run_id=run.run_id,
            provider_name=provider_name,
        )
        targets = phase_execution_targets(brief, agent_id=agent_id)
        self._append_event(
            run,
            event_type="flow.phase.execution.started",
            actor="pal",
            payload={
                "phase": run.current_phase.value,
                "targets": [target.to_dict() for target in targets],
                "brief": dict(brief.paths),
                "policy": policy.value,
                "force_policy": force_policy,
            },
        )

        executions: list[PhaseExecutionRecord] = []
        for target in targets:
            provider = self.provider(target.provider)
            execution_id = self.id_factory("exec")
            prompt = build_execution_prompt(brief, target)
            execution_dir = self.store.phase_execution_dir(
                run.feature,
                run.run_id,
                run.current_phase.value,
                execution_id,
            )
            started_at = self.clock()
            launch = provider.launch_headless(
                ProviderLaunchRequest(
                    run=run,
                    workspace_dir=self.store.feature_dir(run.feature),
                    prompt=prompt,
                    output_dir=execution_dir,
                    writable_dirs=self._provider_writable_dirs(run),
                )
            )
            ended_at = self.clock()
            redacted_command = redact_command_prompt(launch.command, prompt)
            record_without_paths = PhaseExecutionRecord(
                execution_id=execution_id,
                phase=run.current_phase,
                target=target,
                execution_mode=launch.execution_mode,
                status=launch.status,
                returncode=launch.returncode,
                command=redacted_command,
                cwd=launch.cwd,
                started_at=started_at,
                ended_at=ended_at,
                paths={},
                diagnostics=launch.diagnostics,
            )
            paths = self.store.write_phase_execution(
                run,
                phase=run.current_phase.value,
                execution_id=execution_id,
                prompt=prompt,
                stdout=launch.stdout,
                stderr=launch.stderr,
                manifest=record_without_paths.to_dict(),
            )
            executions.append(
                PhaseExecutionRecord(
                    execution_id=execution_id,
                    phase=run.current_phase,
                    target=target,
                    execution_mode=launch.execution_mode,
                    status=launch.status,
                    returncode=launch.returncode,
                    command=redacted_command,
                    cwd=launch.cwd,
                    started_at=started_at,
                    ended_at=ended_at,
                    paths=paths,
                    diagnostics=launch.diagnostics,
                )
            )

        status = execution_status(executions)
        summary = PhaseExecutionSummary(
            run=run,
            phase=run.current_phase,
            status=status,
            brief=brief,
            executions=executions,
        )
        self._append_event(
            run,
            event_type=f"flow.phase.execution.{status}",
            actor="pal",
            payload=summary.to_dict(),
        )
        return summary

    def run_flow(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        provider_name: str = "",
        max_phases: int = 10,
        co_driver_auto_advance: bool = False,
        force_policy: bool = False,
        force_artifacts: bool = False,
    ) -> FlowRunLoopSummary:
        if max_phases < 1:
            raise ValueError("max_phases must be at least 1.")
        if provider_name:
            self.provider(provider_name)

        steps: list[FlowRunStep] = []
        run = self.status(feature, run_id)
        for _ in range(max_phases):
            self._ensure_can_mutate(run)
            workflow = self._stored_workflow(run)
            policy = phase_policy(run)

            if policy == FlowPolicy.OBSERVER and not force_policy:
                self.render_phase(feature, run_id=run.run_id, provider_name=provider_name)
                step = FlowRunStep(
                    phase=run.current_phase,
                    policy=policy,
                    status="observer_stopped",
                    message="observer policy rendered the phase and stopped before execution",
                )
                steps.append(step)
                return self._finish_run_loop(run, "observer_stopped", steps)

            execution = self.execute_phase(
                feature,
                run_id=run.run_id,
                provider_name=provider_name,
                force_policy=force_policy,
            )
            validation = self.check_artifacts(feature, run_id=run.run_id)
            if execution.status != "completed":
                steps.append(
                    FlowRunStep(
                        phase=run.current_phase,
                        policy=policy,
                        status="execution_failed",
                        message="phase execution failed",
                        execution=execution,
                        artifacts=validation,
                    )
                )
                return self._finish_run_loop(run, "failed", steps)
            if not validation.valid and not force_artifacts:
                steps.append(
                    FlowRunStep(
                        phase=run.current_phase,
                        policy=policy,
                        status="missing_artifacts",
                        message="required phase artifacts are missing",
                        execution=execution,
                        artifacts=validation,
                    )
                )
                return self._finish_run_loop(run, "missing_artifacts", steps)
            if self._approval_required_but_missing(run, workflow):
                steps.append(
                    FlowRunStep(
                        phase=run.current_phase,
                        policy=policy,
                        status="waiting_approval",
                        message="phase requires approval before advance",
                        execution=execution,
                        artifacts=validation,
                    )
                )
                return self._finish_run_loop(run, "waiting_approval", steps)
            if not auto_advance_allowed(
                policy,
                co_driver_auto_advance=co_driver_auto_advance,
            ):
                steps.append(
                    FlowRunStep(
                        phase=run.current_phase,
                        policy=policy,
                        status="waiting_human",
                        message=f"{policy.value} policy requires an explicit advance",
                        execution=execution,
                        artifacts=validation,
                    )
                )
                return self._finish_run_loop(run, "waiting_human", steps)

            advanced = self.advance(
                feature,
                run_id=run.run_id,
                force_artifacts=force_artifacts,
                actor="pal-run",
            )
            steps.append(
                FlowRunStep(
                    phase=run.current_phase,
                    policy=policy,
                    status="advanced",
                    message="phase advanced automatically",
                    execution=execution,
                    artifacts=validation,
                    advanced_to=advanced.current_phase,
                )
            )
            run = advanced
            if run.status == FlowStatus.COMPLETED:
                return self._finish_run_loop(run, "completed", steps)

        return self._finish_run_loop(run, "max_phases", steps)

    def approve(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        phase: FlowPhase | None = None,
        reason: str = "",
        actor: str = "human",
    ) -> FlowRun:
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run, allow_blocked=True)
        workflow = self._stored_workflow(run)
        target_phase = phase or run.current_phase
        self._ensure_workflow_phase(workflow, target_phase)
        if target_phase != run.current_phase:
            raise ValueError(
                f"Can only approve current phase '{run.current_phase.value}', "
                f"not '{target_phase.value}'."
            )
        reason = reason.strip()
        verification_outcome = self._verification_outcome_for_phase(
            run,
            workflow,
            target_phase,
        )
        if verification_outcome and verification_outcome.status == VerificationStatus.FAILED:
            raise ValueError(
                f"Phase '{target_phase.value}' has verification status "
                f"'{verification_outcome.status.value}' and cannot be approved."
            )
        phase_requires_approval = self._phase_requires_approval(workflow, target_phase)
        blocked_requires_approval = (
            verification_outcome is not None
            and verification_outcome.status == VerificationStatus.BLOCKED
        )
        if not phase_requires_approval and not blocked_requires_approval:
            raise ValueError(f"Phase '{target_phase.value}' does not require approval.")
        if blocked_requires_approval and not reason:
            raise ValueError(
                f"Phase '{target_phase.value}' has verification status "
                "'blocked' and requires an approval reason."
            )
        now = self.clock()
        approval_reasons = dict(run.approval_reasons)
        if reason:
            approval_reasons[target_phase.value] = reason
        updated = replace(
            run,
            approvals={**run.approvals, target_phase.value: now},
            approval_reasons=approval_reasons,
            updated_at=now,
        )
        self.store.save_state(updated)
        self._append_event(
            updated,
            event_type="flow.phase.approved",
            actor=actor,
            payload={
                "phase": target_phase.value,
                "approved_at": now,
                "reason": reason,
                "verification_status": verification_outcome.status.value
                if verification_outcome
                else "",
            },
        )
        return updated

    def advance(
        self,
        feature: str,
        *,
        run_id: str | None = None,
        signal: str = "complete",
        actor: str = "pal",
        force_artifacts: bool = False,
    ) -> FlowRun:
        run = self.status(feature, run_id)
        self._ensure_can_mutate(run)
        workflow = self._stored_workflow(run)
        signal = signal.strip() or "complete"
        if signal == "complete" and self._approval_required_but_missing(run, workflow):
            raise ValueError(f"Phase '{run.current_phase.value}' requires approval before advance.")
        validation: ArtifactValidation | None = None
        if signal == "complete" and not force_artifacts:
            validation = self._artifact_validation(run, workflow)
            if not validation.valid:
                missing = ", ".join(check.name for check in validation.missing)
                raise ValueError(
                    f"Phase '{run.current_phase.value}' is missing required artifacts: {missing}."
                )
        if signal == "complete":
            validation = validation or self._artifact_validation(run, workflow)
            if validation.valid:
                self._ensure_verification_allows_advance(run, workflow)

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
                payload={
                    "phase": run.current_phase.value,
                    "on": signal,
                    "force_artifacts": force_artifacts,
                },
            )
            return updated

        updated = replace(
            run,
            current_phase=target,
            status=FlowStatus.RUNNING,
            approvals=self._clear_phase_approval(run.approvals, target),
            approval_reasons=self._clear_phase_approval(run.approval_reasons, target),
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
            payload={
                "from": run.current_phase.value,
                "to": target.value,
                "on": signal,
                "force_artifacts": force_artifacts,
            },
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
        self._ensure_workflow_phase(workflow, target)
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
            approvals=self._clear_phase_approval(run.approvals, target),
            approval_reasons=self._clear_phase_approval(run.approval_reasons, target),
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

    def _artifact_validation(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> ArtifactValidation:
        return validate_required_artifacts(
            run,
            workspace_dir=self.store.feature_dir(run.feature),
            required_artifacts=self._phase_required_artifacts(run, workflow),
        )

    def _ensure_verification_allows_advance(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> None:
        if run.current_phase != FlowPhase.VERIFY:
            return
        verification_artifact = self._phase_verification_artifact(run, workflow)
        if not verification_artifact:
            return
        outcome = read_verification_outcome(
            run,
            workspace_dir=self.store.feature_dir(run.feature),
            artifact=verification_artifact,
        )
        if outcome.status == VerificationStatus.PASSED:
            return
        if outcome.status == VerificationStatus.BLOCKED:
            approved = run.current_phase.value in run.approvals
            reason = run.approval_reasons.get(run.current_phase.value, "").strip()
            if approved and reason:
                return
            raise ValueError(
                "Verification completed with status 'blocked'. "
                "Approve the verify phase with a reason before advancing."
            )
        raise ValueError(
            f"Verification status '{outcome.status.value}' cannot advance. "
            "Replan, block the phase, or fix the verification failure."
        )

    def _readiness_summary(self, run: FlowRun) -> FlowReadiness:
        evidence = self.store.read_evidence(run.feature, run.run_id)
        blockers = self._run_status_readiness_blockers(run)
        warnings: list[str] = []
        requirements: list[EvidenceRequirement] = []
        outcome, verification_error = self._readiness_verification_outcome(run)
        if verification_error:
            blockers.append(verification_error)
        elif outcome and outcome.status == VerificationStatus.FAILED:
            blockers.append("Verification failed; fix or re-run verification before merge.")
        elif outcome and outcome.status == VerificationStatus.BLOCKED:
            requirements.extend(self._verification_evidence_requirements(run, outcome))

        latest = _latest_evidence_by_check(evidence)
        for record in latest.values():
            if record.status == EvidenceStatus.FAILED:
                blockers.append(
                    f"Evidence `{record.phase.value}/{record.check}` failed: {record.summary}"
                )
        for requirement in requirements:
            record = latest.get((requirement.phase.value, requirement.check))
            if not record:
                blockers.append(
                    f"Missing evidence `{requirement.phase.value}/{requirement.check}`: "
                    f"{requirement.reason}"
                )
            elif record.status == EvidenceStatus.WAIVED:
                warnings.append(
                    f"Waived evidence `{record.phase.value}/{record.check}`: {record.summary}"
                )
        status = "not_ready" if blockers else "ready"
        return FlowReadiness(
            run_id=run.run_id,
            status=status,
            blockers=blockers,
            warnings=warnings,
            requirements=requirements,
            evidence=evidence,
        )

    def _run_status_readiness_blockers(self, run: FlowRun) -> list[str]:
        if run.status == FlowStatus.COMPLETED:
            return []
        return [f"Run status is `{run.status.value}`, not `completed`."]

    def _readiness_verification_outcome(
        self,
        run: FlowRun,
    ) -> tuple[VerificationOutcome | None, str]:
        artifact = self._readiness_verification_artifact(run)
        if not artifact:
            return None, ""
        try:
            return (
                read_verification_outcome(
                    run,
                    workspace_dir=self.store.feature_dir(run.feature),
                    artifact=artifact,
                ),
                "",
            )
        except VerificationStatusError as exc:
            return None, str(exc)

    def _readiness_verification_artifact(self, run: FlowRun) -> str:
        workflow = self._stored_workflow(run)
        if workflow:
            for phase in workflow.phases:
                if phase.id == FlowPhase.VERIFY:
                    for artifact in phase.required_artifacts:
                        if is_verification_artifact(artifact):
                            return artifact
                    return ""
            return ""
        path = Path(run.artifact_root) / "verification.md"
        return "artifacts/verification.md" if path.exists() else ""

    def _verification_evidence_requirements(
        self,
        run: FlowRun,
        outcome: VerificationOutcome,
    ) -> list[EvidenceRequirement]:
        requirements = _evidence_requirements_from_payload(outcome.payload)
        if requirements:
            return requirements
        reason = str(outcome.payload.get("reason", "")).strip()
        if not reason:
            reason = run.approval_reasons.get(FlowPhase.VERIFY.value, "").strip()
        return [
            EvidenceRequirement(
                phase=FlowPhase.VERIFY,
                check="verification",
                reason=reason or "Verification was approved while blocked.",
            )
        ]

    def _verification_outcome_for_phase(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
        phase: FlowPhase,
    ) -> VerificationOutcome | None:
        if phase != FlowPhase.VERIFY or phase != run.current_phase:
            return None
        verification_artifact = self._phase_verification_artifact(run, workflow)
        if not verification_artifact:
            return None
        return read_verification_outcome(
            run,
            workspace_dir=self.store.feature_dir(run.feature),
            artifact=verification_artifact,
        )

    def _phase_verification_artifact(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> str:
        artifacts = self._phase_required_artifacts(run, workflow)
        for artifact in artifacts:
            if is_verification_artifact(artifact):
                return artifact
        return ""

    def _phase_required_artifacts(
        self,
        run: FlowRun,
        workflow: WorkflowSpec | None,
    ) -> list[str]:
        return list(workflow.phase(run.current_phase).required_artifacts if workflow else [])

    def _finish_run_loop(
        self,
        run: FlowRun,
        status: str,
        steps: list[FlowRunStep],
    ) -> FlowRunLoopSummary:
        summary = FlowRunLoopSummary(run=run, status=status, steps=list(steps))
        self._append_event(
            run,
            event_type=f"flow.run_loop.{status}",
            actor="pal",
            payload=summary.to_dict(),
        )
        return summary

    def _default_pr_body(self, run: FlowRun, readiness: FlowReadiness) -> str:
        implementation = _read_artifact(run, "implementation.md")
        integration = _read_artifact(run, "integration.md")
        verification = _read_artifact(run, "verification.md")
        regression = _read_artifact(run, "regression.md")
        review = _read_artifact(run, "review.md")
        docs_review = _read_artifact(run, "docs-review.md")
        verification_status = _verification_status_from_markdown(verification)
        summary = _first_nonempty_paragraph(run.request)

        lines = [
            f"# {run.feature}",
            "",
            "## Summary",
            "",
            summary or f"Changes for `{run.feature}`.",
            "",
            "## Changes",
            "",
            *_artifact_summary_lines(
                implementation,
                preferred_sections=["Summary", "Changes", "Files Changed", "Files changed"],
                fallback="Implementation artifact has not been written yet.",
            ),
        ]
        if integration:
            lines.extend(
                [
                    "",
                    "## Integration Notes",
                    "",
                    *_artifact_summary_lines(
                        integration,
                        preferred_sections=["Summary", "Compatibility", "Integration"],
                    ),
                ]
            )
        lines.extend(
            [
                "",
                "## Readiness",
                "",
                f"- Status: `{readiness.status}`",
                *_readiness_detail_lines(readiness),
                "",
                "## Validation",
                "",
                f"- Verification status: `{verification_status or 'not recorded'}`",
                *_artifact_summary_lines(
                    verification,
                    preferred_sections=[
                        "Automated Checks",
                        "Validation",
                        "Checks",
                        "Browser Verification",
                        "Manual Verification",
                    ],
                    fallback="- Verification artifact has not been written yet.",
                ),
            ]
        )
        if regression:
            lines.extend(
                [
                    "",
                    "## Regression Coverage",
                    "",
                    *_artifact_summary_lines(
                        regression,
                        preferred_sections=["Summary", "Risks", "Coverage"],
                    ),
                ]
            )
        lines.extend(
            [
                "",
                "## Review",
                "",
                *_artifact_summary_lines(
                    review,
                    preferred_sections=["Summary", "Findings", "Review"],
                    fallback="Review phase has not completed yet.",
                ),
            ]
        )
        if docs_review:
            lines.extend(
                [
                    "",
                    "## Docs And Operations",
                    "",
                    *_artifact_summary_lines(
                        docs_review,
                        preferred_sections=["Summary", "Docs", "Operations"],
                    ),
                ]
            )
        blocked_notes = _blocked_verification_lines(run, verification_status, verification)
        if blocked_notes:
            lines.extend(["", "## Risks And Follow-Ups", "", *blocked_notes])
        lines.extend(
            [
                "",
                "## Flow Metadata",
                "",
                f"- Run ID: `{run.run_id}`",
                f"- Status: `{run.status.value}`",
                f"- Current phase: `{run.current_phase.value}`",
                f"- Mode: `{run.mode}`",
            ]
        )
        if run.workflow_name:
            lines.append(f"- Workflow: `{run.workflow_name}`")
        if run.work_type:
            lines.append(f"- Work type: `{run.work_type}`")
        if run.repos:
            lines.append(f"- Repos: {', '.join(f'`{repo}`' for repo in run.repos)}")
        return "\n".join(lines)

    def _normalize_repos(self, repos: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_repo_name(repo) for repo in repos))

    def _write_pr_phase_artifact(self, run: FlowRun, summary: FlowPrSummary) -> Path:
        path = resolve_artifact_path(run, self.store.feature_dir(run.feature), "artifacts/pr.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_pr_phase_artifact_markdown(summary), encoding="utf-8")
        return path

    def _provider_writable_dirs(self, run: FlowRun) -> list[Path]:
        feature_dir = self.store.feature_dir(run.feature)
        dirs: list[Path] = []
        for repo in run.repos:
            repo_path = safe_child_path(
                feature_dir,
                normalize_repo_name(repo),
                "Repo name",
            )
            if repo_path.exists():
                dirs.extend(git_metadata_dirs(repo_path))
        return list(dict.fromkeys(dirs))

    def _ensure_workflow_phase(
        self,
        workflow: WorkflowSpec | None,
        phase: FlowPhase,
    ) -> None:
        if workflow:
            workflow.phase(phase)

    def _clear_phase_approval(
        self,
        approvals: dict[str, str],
        phase: FlowPhase,
    ) -> dict[str, str]:
        if phase.value not in approvals:
            return dict(approvals)
        updated = dict(approvals)
        del updated[phase.value]
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
                for tool in [*agent.tools.required, *agent.tools.optional]:
                    if tool not in _DELEGATED_TOOLS:
                        errors.append(f"Agent '{agent.id}' has unknown delegated tool '{tool}'.")
        return errors

    def _workflow_artifact_errors(self, spec: WorkflowSpec) -> list[str]:
        errors: list[str] = []
        for phase in spec.phases:
            for artifact in phase.required_artifacts:
                try:
                    validate_artifact_reference(artifact)
                except ArtifactPathError as exc:
                    errors.append(
                        f"Phase '{phase.id.value}' has invalid required artifact "
                        f"'{artifact}': {exc}"
                    )
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
        self.hooks.dispatch(event=event, run=run, store=self.store)


def _latest_evidence_by_check(
    evidence: list[FlowEvidence],
) -> dict[tuple[str, str], FlowEvidence]:
    latest: dict[tuple[str, str], FlowEvidence] = {}
    for record in evidence:
        latest[(record.phase.value, record.check)] = record
    return latest


def _evidence_requirements_from_payload(payload: dict[str, object]) -> list[EvidenceRequirement]:
    raw_items = payload.get("required_evidence", payload.get("blocked_checks", []))
    return _requirements_from_items(raw_items)


def _requirements_from_items(items: object) -> list[EvidenceRequirement]:
    if not isinstance(items, list):
        return []
    requirements: list[EvidenceRequirement] = []
    for item in items:
        requirement = _requirement_from_item(item)
        if requirement:
            requirements.append(requirement)
    return requirements


def _requirement_from_item(item: object) -> EvidenceRequirement | None:
    if isinstance(item, str):
        return EvidenceRequirement(
            phase=FlowPhase.VERIFY,
            check=normalize_evidence_check(item),
            reason="Required verification evidence is missing.",
        )
    if not isinstance(item, dict):
        return None
    check = str(item.get("check") or item.get("name") or item.get("id") or "").strip()
    if not check:
        return None
    return EvidenceRequirement(
        phase=FlowPhase(str(item.get("phase", FlowPhase.VERIFY.value))),
        check=normalize_evidence_check(check),
        reason=str(item.get("reason") or item.get("summary") or "Required evidence is missing."),
    )


def _readiness_detail_lines(readiness: FlowReadiness) -> list[str]:
    lines: list[str] = []
    lines.extend(f"- Blocker: {blocker}" for blocker in readiness.blockers)
    lines.extend(f"- Warning: {warning}" for warning in readiness.warnings)
    lines.extend(
        f"- Required evidence: `{requirement.phase.value}/{requirement.check}` - {requirement.reason}"
        for requirement in readiness.requirements
    )
    lines.extend(
        f"- Evidence: `{record.phase.value}/{record.check}` `{record.status.value}` - {record.summary}"
        for record in readiness.evidence
    )
    return lines or ["- No unresolved blockers."]


def _read_artifact(run: FlowRun, name: str) -> str:
    path = Path(run.artifact_root) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _artifact_summary_lines(
    markdown: str,
    *,
    preferred_sections: list[str],
    fallback: str = "",
    limit: int = 1600,
) -> list[str]:
    if not markdown.strip():
        return [fallback] if fallback else []

    sections: list[str] = []
    for heading in preferred_sections:
        section = _markdown_section(markdown, heading)
        if section:
            sections.append(section)
    content = _strip_json_blocks("\n\n".join(dict.fromkeys(sections))).strip()
    if not content:
        content = _first_nonempty_paragraph(_strip_json_blocks(markdown))
    if not content:
        return [fallback] if fallback else []
    return _truncate_markdown(content, limit).splitlines()


def _markdown_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    wanted = heading.strip().lower()
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if not match or match.group(2).strip().lower() != wanted:
            continue
        level = len(match.group(1))
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            next_match = re.match(r"^(#{1,6})\s+", lines[next_index])
            if next_match and len(next_match.group(1)) <= level:
                end = next_index
                break
        return "\n".join(lines[index + 1 : end]).strip()
    return ""


def _first_nonempty_paragraph(text: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text.strip())]
    for paragraph in paragraphs:
        cleaned = "\n".join(
            line.strip()
            for line in paragraph.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ).strip()
        if cleaned:
            return cleaned
    return ""


def _strip_json_blocks(markdown: str) -> str:
    return re.sub(
        r"```json\s*.*?```",
        "",
        markdown,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _truncate_markdown(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    truncated = stripped[: limit - 22].rstrip()
    if truncated.count("```") % 2:
        truncated += "\n```"
    return truncated + "\n\n...truncated for PR body"


def _verification_status_from_markdown(markdown: str) -> str:
    for candidate in _json_object_candidates(markdown):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
            return str(parsed["status"])
    return ""


def _json_object_candidates(markdown: str) -> list[str]:
    candidates = re.findall(
        r"```(?:json)?\s*(.*?)\s*```",
        markdown,
        flags=re.DOTALL | re.IGNORECASE,
    )
    stripped = markdown.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    return candidates


def _blocked_verification_lines(
    run: FlowRun,
    verification_status: str,
    verification: str,
) -> list[str]:
    if verification_status != VerificationStatus.BLOCKED.value:
        return []
    reason = run.approval_reasons.get(FlowPhase.VERIFY.value, "").strip()
    lines = [
        "- Verification status is `blocked`; reviewers should inspect the validation limitation before merge.",
    ]
    if reason:
        lines.append(f"- Human approval reason: {reason}")
    if _markdown_section(verification, "Browser Verification"):
        lines.append("- Browser verification details are recorded in the Validation section.")
    return lines


def _pr_phase_artifact_markdown(summary: FlowPrSummary) -> str:
    urls = [repo.pr_url for repo in summary.repos if repo.pr_url]
    lines = [
        "# PR Phase",
        "",
        "```json",
        json.dumps(summary.to_dict(), indent=2, sort_keys=True),
        "```",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.status}`",
        f"- Readiness: `{summary.readiness.get('status', 'unknown')}`",
        f"- Branches: {', '.join(f'`{repo.branch}`' for repo in summary.repos) or '`none`'}",
        f"- PR URLs: {', '.join(urls) if urls else '`none`'}",
        "",
        "## Repos",
        "",
    ]
    for repo in summary.repos:
        lines.extend(
            [
                f"- `{repo.repo}`",
                f"  - Commit: `{repo.commit_status}`",
                f"  - Push: `{repo.push_status}`",
                f"  - PR: `{repo.pr_status}`",
                f"  - URL: `{repo.pr_url or 'none'}`",
                f"  - Error: `{repo.error or 'none'}`",
            ]
        )
    return "\n".join(lines) + "\n"
