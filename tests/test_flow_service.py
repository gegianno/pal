from __future__ import annotations

from pathlib import Path

import pytest

import pal.flow.service as service_module
from pal.flow.hooks import FlowHook, FlowHookDispatcher, HookCommandResult
from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import ProviderLaunchRequest, ProviderLaunchResult
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.service import LocalFlowService, default_policies
from pal.flow.pr import FlowPrRepo, FlowPrSummary
from pal.flow.store import LocalFlowStore
from pal.flow.workflows.library import LocalWorkflowLibrary, WorkflowSpecError
from pal.workspaces import WorkspacePrepareResult, WorkspaceRepoResult


def _ids() -> list[str]:
    return ["run_test", "evt_started", "evt_provider", "session_test", "evt_completed"]


class FailingFlowProvider(FakeFlowProvider):
    name = "failing"

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        return ProviderLaunchResult(
            provider=self.name,
            execution_mode="local_headless",
            command=["failing-flow-provider", request.prompt],
            cwd=str(request.workspace_dir),
            status="failed",
            returncode=9,
            stdout="",
            stderr="failed\n",
        )


class RecordingFlowProvider(FakeFlowProvider):
    def __init__(self) -> None:
        self.requests: list[ProviderLaunchRequest] = []

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        self.requests.append(request)
        return super().launch_headless(request)


class RecordingHookRunner:
    def run(self, command, *, cwd=None, env=None, timeout=None):  # noqa: ANN001, ANN201
        return HookCommandResult(
            returncode=0,
            stdout=f"{command[0]}:{env['PAL_FLOW_EVENT_TYPE']}:{timeout}\n",
        )


class RecordingWorkspaceBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def prepare(
        self,
        *,
        feature: str,
        repos: list[str],
        mode: str,
        copy_local: bool | None = None,
        overwrite_local: bool | None = None,
    ) -> WorkspacePrepareResult:
        self.calls.append(
            {
                "feature": feature,
                "repos": list(repos),
                "mode": mode,
                "copy_local": copy_local,
                "overwrite_local": overwrite_local,
            }
        )
        return WorkspacePrepareResult(
            feature=feature,
            mode=mode,
            workspace_dir="/tmp/_wt/feat",
            workspace_file="/tmp/_wt/feat/feat.code-workspace",
            repos=[
                WorkspaceRepoResult(
                    repo=repo,
                    source_path=f"/tmp/{repo}",
                    worktree_path=f"/tmp/_wt/feat/{repo}",
                    branch="feat/feat",
                    status="created",
                )
                for repo in repos
            ],
        )


class RecordingPrManager:
    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed
        self.calls: list[dict[str, object]] = []

    def prepare(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(dict(kwargs))
        return FlowPrSummary(
            feature=str(kwargs["feature"]),
            run_id=str(kwargs["run_id"]),
            base=str(kwargs["base"]),
            title=str(kwargs["title"]),
            dry_run=bool(kwargs["dry_run"]),
            commit_requested=bool(kwargs["commit"]),
            push_requested=bool(kwargs["push"]),
            pr_requested=bool(kwargs["create_pr"]),
            draft=bool(kwargs["draft"]),
            repos=[
                FlowPrRepo(
                    repo="api",
                    path="/tmp/_wt/feat/api",
                    branch="feat/feat",
                    base=str(kwargs["base"]),
                    head_sha="abc123",
                    changed=True,
                    status_short="## feat/feat",
                    porcelain=" M README.md",
                    diff_stat="README.md | 1 +",
                    error="failed" if self.failed else "",
                )
            ],
        )


def _write_workflow(root: Path, name: str, body: str) -> Path:
    path = root / ".pal" / "flows" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _workflow_body(
    *,
    provider: str = "fake",
    requirement: str = "local_headless",
    agent_provider: str = "",
    design_policy: str = "co-driver",
    design_required_artifact: bool = False,
    design_requires_approval: bool = False,
    implement_blocked_transition: bool = False,
) -> str:
    agent_provider_line = f"        provider: {agent_provider}\n" if agent_provider else ""
    required_artifact_lines = (
        "    required_artifacts:\n      - artifacts/design.md\n" if design_required_artifact else ""
    )
    approval_line = "    requires_approval: true\n" if design_requires_approval else ""
    transition_lines = (
        "    transitions:\n      - on: blocked\n        to: design\n"
        if implement_blocked_transition
        else ""
    )
    return f"""
version: 1
name: dev-complex
work_type: dev
mode: complex
repos:
  - api
defaults:
  provider: {provider}
phases:
  - id: design
    policy: {design_policy}
{required_artifact_lines}{approval_line}    transitions:
      - on: complete
        to: implement
    agents:
      - id: designer
        role: design
{agent_provider_line}        requires:
          - {requirement}
  - id: implement
{transition_lines}    agents: []
"""


def _single_phase_workflow(*, policy: str = "autonomous", approval: bool = False) -> str:
    approval_line = "    requires_approval: true\n" if approval else ""
    return f"""
version: 1
name: single
work_type: dev
defaults:
  provider: fake
phases:
  - id: design
    policy: {policy}
{approval_line}    agents: []
"""


def _verify_workflow() -> str:
    return """
version: 1
name: verify-flow
work_type: dev
defaults:
  provider: fake
phases:
  - id: verify
    policy: supervisor
    required_artifacts:
      - artifacts/verification.md
    transitions:
      - on: complete
        to: pr
    agents: []
  - id: pr
    agents: []
"""


def _verify_workflow_without_verification_artifact() -> str:
    return """
version: 1
name: verify-without-verification-artifact
work_type: dev
defaults:
  provider: fake
phases:
  - id: verify
    requires_approval: true
    required_artifacts:
      - artifacts/notes.md
      - artifacts/evidence.md
    transitions:
      - on: complete
        to: pr
    agents: []
  - id: pr
    agents: []
"""


def _write_verification_artifact(run: FlowRun, status: str) -> None:
    root = Path(run.artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "verification.md").write_text(
        f'# Verification\n\n```json\n{{"status": "{status}"}}\n```\n',
        encoding="utf-8",
    )


def test_default_policies_match_routine_flow_defaults() -> None:
    policies = default_policies()

    assert policies["explore"].value == "autonomous"
    assert policies["verify"].value == "supervisor"
    assert policies["pr"].value == "supervisor"
    assert policies["review"].value == "supervisor"


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


def test_flow_service_start_persists_user_request(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
    )

    run = service.start(
        feature="feat",
        repos=[],
        request="  Fix the multi-select border and focus styling.  ",
    )

    assert run.request == "Fix the multi-select border and focus styling."
    assert service.status("feat").request == "Fix the multi-select border and focus styling."
    assert store.read_run_json("feat", run.run_id, "request.json") == {
        "request": "Fix the multi-select border and focus styling."
    }
    assert service.events("feat")[0].payload["request_chars"] == len(run.request)


def test_flow_service_dispatches_configured_hooks_for_events(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        hooks=FlowHookDispatcher(
            hooks=[FlowHook(name="notify", command=["notify"])],
            runner=RecordingHookRunner(),
            timeout=7,
        ),
        clock=lambda: "2026-04-27T00:00:00Z",
    )

    run = service.start(feature="feat", repos=[])

    results = service.hook_results("feat", run.run_id)
    assert len(results) == 2
    assert results[0]["hook"] == "notify"
    assert results[0]["stdout"] == "notify:flow.run.started:7\n"


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
    assert sessions[0]["launch_command"] == ["fake-flow-provider", "<prompt>"]
    assert (
        store.read_run_json("feat", run.run_id, "provider-auth.json")["fake"]["status"]
        == "available"
    )
    assert store.read_run_json("feat", run.run_id, "provider-capabilities.json")["fake"][
        "execution_modes"
    ] == ["local_headless"]


def test_flow_service_workflow_paths_and_missing_library(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )

    assert service.workflow_paths() == []
    result = service.validate_workflow("missing")
    assert result.valid is False
    assert "not configured" in result.errors[0]
    with pytest.raises(WorkflowSpecError, match="not configured"):
        service.load_workflow("missing")


def test_flow_service_validates_workflow_provider_references(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body(provider="missing"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("dev-complex")

    assert result.valid is False
    assert result.name == "dev-complex"
    assert result.work_type == "dev"
    assert "Unknown provider 'missing'" in result.errors[0]
    assert service.validate_workflows() == [result]


def test_flow_service_validates_agent_capability_requirements(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "capabilities",
        _workflow_body(provider="fake", requirement="hooks", agent_provider="fake"),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("capabilities")

    assert result.valid is False
    assert "requires 'hooks'" in result.errors[0]


def test_flow_service_accepts_supported_agent_capability_flags(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "json-output", _workflow_body(requirement="json_output"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("json-output")

    assert result.valid is True


def test_flow_service_accepts_known_delegated_tools(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "github-write",
        """
version: 1
name: github-write
work_type: dev
defaults:
  provider: fake
phases:
  - id: pr
    agents:
      - id: pr-manager
        role: PR manager
        requires:
          - local_headless
        tools:
          required:
            - github_write
          optional:
            - linear_write
""",
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("github-write")

    assert result.valid is True


def test_flow_service_validates_unknown_delegated_tools(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "unknown-tool",
        """
version: 1
name: unknown-tool
work_type: dev
defaults:
  provider: fake
phases:
  - id: pr
    agents:
      - id: pr-manager
        role: PR manager
        tools:
          required:
            - figma_write
""",
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("unknown-tool")

    assert result.valid is False
    assert "unknown delegated tool 'figma_write'" in result.errors[0]


def test_flow_service_validates_unknown_agent_requirement(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "unknown-req", _workflow_body(requirement="quantum_gpu"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("unknown-req")

    assert result.valid is False
    assert "unknown requirement 'quantum_gpu'" in result.errors[0]


def test_flow_service_validates_required_artifact_paths(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "bad-artifact",
        """
version: 1
name: bad-artifact
work_type: dev
defaults:
  provider: fake
phases:
  - id: design
    required_artifacts:
      - ../outside.md
    agents: []
""",
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    result = service.validate_workflow("bad-artifact")

    assert result.valid is False
    assert "invalid required artifact" in result.errors[0]
    assert "must not contain '..'" in result.errors[0]


def test_flow_service_start_with_workflow_persists_metadata(tmp_path: Path) -> None:
    ids = _ids()
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )

    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    assert run.workflow_name == "dev-complex"
    assert run.work_type == "dev"
    assert run.mode == "complex"
    assert run.repos == ["api"]
    assert run.current_phase == FlowPhase.DESIGN
    assert set(run.policies) == {"design", "implement"}
    assert run.policies["design"].value == "co-driver"
    assert run.policies["implement"].value == "autonomous"
    assert store.read_run_json("feat", run.run_id, "workflow.json")["name"] == "dev-complex"
    assert service.events("feat")[0].payload["workflow"] == "dev-complex"


def test_flow_service_start_can_prepare_real_workspace(tmp_path: Path) -> None:
    workspace = RecordingWorkspaceBackend()
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workspace_backend=workspace,
    )

    run = service.start(
        feature="feat",
        repos=["api"],
        workspace_mode="reuse",
        copy_local=True,
        overwrite_local=False,
    )

    assert workspace.calls == [
        {
            "feature": "feat",
            "repos": ["api"],
            "mode": "reuse",
            "copy_local": True,
            "overwrite_local": False,
        }
    ]
    workspace_json = service.store.read_run_json("feat", run.run_id, "workspace.json")
    assert isinstance(workspace_json, dict)
    assert workspace_json["mode"] == "reuse"
    assert workspace_json["repos"][0]["repo"] == "api"
    assert [event.type for event in service.events("feat")] == [
        "flow.run.started",
        "flow.workspace.prepared",
        "provider.started",
    ]


def test_flow_service_start_rejects_workspace_modes_without_backend(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )

    with pytest.raises(ValueError, match="Workspace backend is not configured"):
        service.start(feature="feat", repos=[], workspace_mode="reuse")


def test_flow_service_pr_writes_body_manifest_and_event(tmp_path: Path) -> None:
    pr_manager = RecordingPrManager()
    store = LocalFlowStore(tmp_path / "_wt")
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        pr_manager=pr_manager,
    )
    run = service.start(feature="feat", repos=["api"], mode="complex")

    summary = service.pr(
        "feat",
        base="develop",
        title="",
        body="custom body",
        dry_run=False,
        push=True,
        create_pr=True,
        draft=True,
    )

    body_path = store.pr_dir("feat", run.run_id) / "body.md"
    manifest_path = store.pr_dir("feat", run.run_id) / "manifest.json"
    artifact_path = Path(run.artifact_root) / "pr.md"
    assert summary.status == "completed"
    assert pr_manager.calls[0]["repos"] == ["api"]
    assert pr_manager.calls[0]["feature_dir"] == store.feature_dir("feat")
    assert pr_manager.calls[0]["base"] == "develop"
    assert pr_manager.calls[0]["title"] == "feat: complex"
    assert body_path.read_text(encoding="utf-8") == "custom body\n"
    assert manifest_path.is_file()
    assert artifact_path.is_file()
    assert summary.paths == {
        "artifact": str(artifact_path),
        "body": str(body_path),
        "manifest": str(manifest_path),
    }
    assert store.read_run_json("feat", run.run_id, "pr/manifest.json")["paths"] == summary.paths
    assert "## Summary" in artifact_path.read_text(encoding="utf-8")
    assert service.events("feat")[-1].type == "flow.pr.completed"


def test_flow_service_pr_uses_default_body_and_reports_failures(tmp_path: Path) -> None:
    pr_manager = RecordingPrManager(failed=True)
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        pr_manager=pr_manager,
    )
    run = service.start(
        feature="feat",
        repos=[],
        workflow="dev-complex",
        request="Fix the select styling regression.\n\nKeep behavior unchanged.",
    )
    artifact_root = Path(run.artifact_root)
    artifact_root.mkdir(parents=True)
    (artifact_root / "implementation.md").write_text(
        "# Implementation\n\n"
        "```json\n"
        '{"files": ["ui/select.tsx"]}\n'
        "```\n\n"
        "## Summary\n\n"
        "- Restored select borders and focus styling.\n\n"
        "## Files Changed\n\n"
        "- `ui/select.tsx`\n",
        encoding="utf-8",
    )
    (artifact_root / "integration.md").write_text(
        "## Compatibility\n\nNo API contract changes.\n",
        encoding="utf-8",
    )
    (artifact_root / "verification.md").write_text(
        "# Verification\n\n"
        "```json\n"
        '{"status": "blocked"}\n'
        "```\n\n"
        "## Automated Checks\n\n"
        "- `npm test` passed.\n\n"
        "## Browser Verification\n\n"
        "- Browser launch was blocked by sandbox limits.\n",
        encoding="utf-8",
    )
    (artifact_root / "regression.md").write_text(
        "No additional regression risk found.\n",
        encoding="utf-8",
    )
    (artifact_root / "docs-review.md").write_text(
        "## Summary\n\nNo docs updates required.\n",
        encoding="utf-8",
    )

    summary = service.pr("feat", repos=["api"], base=" ", dry_run=True)

    body = (store.pr_dir("feat", run.run_id) / "body.md").read_text(encoding="utf-8")
    assert summary.status == "failed"
    assert "## Summary" in body
    assert "Fix the select styling regression." in body
    assert "## Changes" in body
    assert "Restored select borders" in body
    assert "## Integration Notes" in body
    assert "No API contract changes." in body
    assert "## Validation" in body
    assert "Verification status: `blocked`" in body
    assert "`npm test` passed" in body
    assert "Browser launch was blocked" in body
    assert "## Regression Coverage" in body
    assert "No additional regression risk found." in body
    assert "## Docs And Operations" in body
    assert "No docs updates required." in body
    assert "## Risks And Follow-Ups" in body
    assert "Verification status is `blocked`" in body
    assert "Workflow: `dev-complex`" in body
    assert "Work type: `dev`" in body
    assert "Repos: `api`" in body
    assert (Path(run.artifact_root) / "pr.md").is_file()
    assert pr_manager.calls[0]["base"] == "main"
    assert service.events("feat")[-1].type == "flow.pr.failed"


def test_flow_service_pr_requires_message_when_committing(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    with pytest.raises(ValueError, match="--message is required"):
        service.pr("feat", commit=True, commit_message=" ")


def test_pr_body_helpers_handle_missing_fallbacks_and_status_variants() -> None:
    run = FlowRun.from_dict(
        {
            **FlowRun(
                run_id="run_1",
                feature="feat",
                mode="routine",
                repos=[],
                current_phase=FlowPhase.PR,
                status=FlowStatus.RUNNING,
                policies={},
                artifact_root="/tmp/feat/.pal/artifacts",
                created_at="now",
                updated_at="now",
            ).to_dict(),
            "approval_reasons": {"verify": "Browser check completed manually."},
        }
    )

    assert service_module._artifact_summary_lines(
        "", preferred_sections=[], fallback="missing"
    ) == ["missing"]
    assert service_module._artifact_summary_lines(
        '# Notes\n\n```json\n{"ignored": true}\n```\n\nUseful paragraph.',
        preferred_sections=["Missing"],
    ) == ["Useful paragraph."]
    assert service_module._artifact_summary_lines(
        '## Summary\n\nNo code findings.\n\n```json\n{"status": "approved"}\n```',
        preferred_sections=["Summary"],
    ) == ["No code findings."]
    assert (
        service_module._artifact_summary_lines(
            "## Summary\n\n" + ("x" * 1700),
            preferred_sections=["Summary"],
        )[-1]
        == "...truncated for PR body"
    )
    assert "```\n\n...truncated" in "\n".join(
        service_module._artifact_summary_lines(
            "## Summary\n\n```text\n" + ("x" * 1700),
            preferred_sections=["Summary"],
        )
    )
    assert service_module._artifact_summary_lines(
        '```json\n{"ignored": true}\n```',
        preferred_sections=["Missing"],
        fallback="fallback",
    ) == ["fallback"]
    assert service_module._first_nonempty_paragraph("## Heading\n\n") == ""
    assert service_module._verification_status_from_markdown('{"status": "passed"}') == "passed"
    assert (
        service_module._verification_status_from_markdown(
            '```json\n{"other": {"nested": true}}\n```\n\n'
            '```json\n{"status": "blocked", "checks": [{"name": "browser"}]}\n```'
        )
        == "blocked"
    )
    assert service_module._verification_status_from_markdown("```json\n{bad}\n```") == ""
    assert service_module._blocked_verification_lines(run, "passed", "") == []
    assert service_module._blocked_verification_lines(
        FlowRun.from_dict({**run.to_dict(), "approval_reasons": {}}),
        "blocked",
        "",
    ) == [
        "- Verification status is `blocked`; reviewers should inspect the validation limitation before merge.",
    ]
    assert service_module._blocked_verification_lines(
        run,
        "blocked",
        "## Browser Verification\n\nBlocked by sandbox.",
    ) == [
        "- Verification status is `blocked`; reviewers should inspect the validation limitation before merge.",
        "- Human approval reason: Browser check completed manually.",
        "- Browser verification details are recorded in the Validation section.",
    ]


def test_flow_service_start_allows_cli_overrides_for_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    run = service.start(
        feature="feat",
        repos=["web"],
        mode="routine",
        phase=FlowPhase.IMPLEMENT,
        provider_name="fake",
        workflow="dev-complex",
    )

    assert run.mode == "routine"
    assert run.repos == ["web"]
    assert run.current_phase == FlowPhase.IMPLEMENT


def test_flow_service_start_rejects_phase_missing_from_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "single", _single_phase_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    with pytest.raises(ValueError, match="has no phase 'explore'"):
        service.start(feature="feat", repos=[], workflow="single", phase=FlowPhase.EXPLORE)

    assert not (tmp_path / "_wt" / "feat" / ".pal").exists()


def test_flow_service_start_rejects_invalid_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "bad", _workflow_body(provider="missing"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )

    with pytest.raises(ValueError, match="Workflow spec is invalid"):
        service.start(feature="feat", repos=[], workflow="bad")


def test_flow_service_render_phase_writes_brief_artifacts_and_event(tmp_path: Path) -> None:
    ids = _ids()
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_required_artifact=True, design_requires_approval=True),
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    brief = service.render_phase("feat")

    assert brief.paths == {
        "markdown": str(store.phase_dir("feat", run.run_id, "design") / "brief.md"),
        "json": str(store.phase_dir("feat", run.run_id, "design") / "brief.json"),
    }
    assert (
        Path(brief.paths["markdown"])
        .read_text(encoding="utf-8")
        .startswith("# pal flow phase brief")
    )
    data = store.read_run_json("feat", run.run_id, "phase/design/brief.json")
    assert isinstance(data, dict)
    assert data["phase"]["required_artifacts"] == ["artifacts/design.md"]
    assert data["phase"]["requires_approval"] is True
    assert data["paths"] == brief.paths
    assert service.events("feat")[-1].type == "flow.phase.rendered"
    assert service.events("feat")[-1].payload["providers"] == ["fake"]


def test_flow_service_render_phase_filters_provider_and_rejects_unknown_provider(
    tmp_path: Path,
) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    brief = service.render_phase("feat", provider_name="fake")

    assert list(brief.provider_guidance) == ["fake"]
    with pytest.raises(ValueError, match="Unknown provider"):
        service.render_phase("feat", provider_name="missing")


def test_flow_service_checks_artifacts_and_advance_enforces_required_artifacts(
    tmp_path: Path,
) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body(design_required_artifact=True))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    missing = service.check_artifacts("feat")

    assert missing.valid is False
    assert missing.missing[0].name == "artifacts/design.md"
    assert service.events("feat")[-1].type == "flow.artifacts.checked"
    with pytest.raises(ValueError, match="missing required artifacts"):
        service.advance("feat")

    Path(run.artifact_root).mkdir(parents=True, exist_ok=True)
    (Path(run.artifact_root) / "design.md").write_text("ok\n", encoding="utf-8")
    valid = service.check_artifacts("feat")
    advanced = service.advance("feat")

    assert valid.valid is True
    assert advanced.current_phase == FlowPhase.IMPLEMENT


def test_flow_service_advance_can_force_missing_artifacts(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body(design_required_artifact=True))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    advanced = service.advance("feat", force_artifacts=True)

    assert advanced.current_phase == FlowPhase.IMPLEMENT
    assert service.events("feat")[-1].payload["force_artifacts"] is True


def test_flow_service_verify_passed_status_can_advance(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "verify-flow", _verify_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    run = service.start(
        feature="feat",
        repos=[],
        workflow="verify-flow",
        phase=FlowPhase.VERIFY,
    )
    _write_verification_artifact(run, "passed")

    advanced = service.advance("feat")

    assert advanced.current_phase == FlowPhase.PR


def test_flow_service_verify_without_verification_artifact_uses_normal_gates(
    tmp_path: Path,
) -> None:
    _write_workflow(
        tmp_path,
        "verify-without-verification-artifact",
        _verify_workflow_without_verification_artifact(),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    run = service.start(
        feature="feat",
        repos=[],
        workflow="verify-without-verification-artifact",
        phase=FlowPhase.VERIFY,
    )
    artifact_root = Path(run.artifact_root)
    artifact_root.mkdir(parents=True)
    (artifact_root / "notes.md").write_text("notes\n", encoding="utf-8")
    (artifact_root / "evidence.md").write_text("evidence\n", encoding="utf-8")

    approved = service.approve("feat", reason="Manual verification artifact is not required.")
    advanced = service.advance("feat")

    assert approved.approval_reasons == {"verify": "Manual verification artifact is not required."}
    assert advanced.current_phase == FlowPhase.PR


def test_flow_service_verify_blocked_status_requires_approval_reason(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "verify-flow", _verify_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    run = service.start(
        feature="feat",
        repos=[],
        workflow="verify-flow",
        phase=FlowPhase.VERIFY,
    )
    _write_verification_artifact(run, "blocked")

    with pytest.raises(ValueError, match="blocked"):
        service.advance("feat")
    with pytest.raises(ValueError, match="approval reason"):
        service.approve("feat")
    approved = service.approve("feat", reason="Browser check was blocked in sandbox.")
    advanced = service.advance("feat")

    assert approved.approvals == {"verify": "2026-04-27T00:00:00Z"}
    assert approved.approval_reasons == {"verify": "Browser check was blocked in sandbox."}
    assert advanced.current_phase == FlowPhase.PR
    assert service.events("feat")[-2].payload == {
        "phase": "verify",
        "approved_at": "2026-04-27T00:00:00Z",
        "reason": "Browser check was blocked in sandbox.",
        "verification_status": "blocked",
    }


def test_flow_service_verify_failed_status_cannot_advance_or_approve(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "verify-flow", _verify_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    run = service.start(
        feature="feat",
        repos=[],
        workflow="verify-flow",
        phase=FlowPhase.VERIFY,
    )
    _write_verification_artifact(run, "failed")

    with pytest.raises(ValueError, match="Verification status 'failed' cannot advance"):
        service.advance("feat")
    with pytest.raises(ValueError, match="verification status 'failed' and cannot be approved"):
        service.approve("feat", reason="Accept anyway.")


def test_flow_service_execute_phase_runs_rendered_agents_and_records_artifacts(
    tmp_path: Path,
) -> None:
    ids = [
        "run_exec",
        "evt_started",
        "evt_provider",
        "evt_rendered",
        "evt_execution_started",
        "exec_designer",
        "evt_execution_completed",
    ]
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_required_artifact=True),
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    summary = service.execute_phase("feat")

    assert summary.status == "completed"
    assert summary.to_dict()["brief_paths"] == summary.brief.paths
    assert len(summary.executions) == 1
    execution = summary.executions[0]
    assert execution.execution_id == "exec_designer"
    assert execution.target.agent_id == "designer"
    assert execution.paths == {
        "prompt": str(
            store.phase_execution_dir("feat", run.run_id, "design", "exec_designer") / "prompt.md"
        ),
        "stdout": str(
            store.phase_execution_dir("feat", run.run_id, "design", "exec_designer") / "stdout.log"
        ),
        "stderr": str(
            store.phase_execution_dir("feat", run.run_id, "design", "exec_designer") / "stderr.log"
        ),
        "manifest": str(
            store.phase_execution_dir("feat", run.run_id, "design", "exec_designer")
            / "manifest.json"
        ),
    }
    assert "pal flow execution request" in Path(execution.paths["prompt"]).read_text(
        encoding="utf-8"
    )
    assert (
        Path(execution.paths["stdout"])
        .read_text(encoding="utf-8")
        .startswith("fake provider completed")
    )
    manifest = store.read_run_json(
        "feat",
        run.run_id,
        "phase/design/executions/exec_designer/manifest.json",
    )
    assert isinstance(manifest, dict)
    assert manifest["target"]["agent_id"] == "designer"
    assert manifest["paths"] == execution.paths
    assert manifest["command"][-1] == "<prompt>"
    assert execution.command[-1] == "<prompt>"
    assert manifest["diagnostics"]["executable"] == "fake-flow-provider"
    assert [event.type for event in service.events("feat")][-3:] == [
        "flow.phase.rendered",
        "flow.phase.execution.started",
        "flow.phase.execution.completed",
    ]


def test_flow_service_passes_repo_git_metadata_dirs_to_phase_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = [
        "run_exec",
        "evt_started",
        "evt_provider",
        "evt_rendered",
        "evt_execution_started",
        "exec_designer",
        "evt_execution_completed",
    ]
    store = LocalFlowStore(tmp_path / "_wt")
    provider = RecordingFlowProvider()
    metadata_dir = tmp_path / "llamadapro" / ".git" / "worktrees" / "feat"
    common_dir = tmp_path / "llamadapro" / ".git"
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_required_artifact=True),
    )
    monkeypatch.setattr(
        service_module,
        "git_metadata_dirs",
        lambda _repo_path: [metadata_dir, common_dir, metadata_dir],
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": provider},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    service.start(feature="feat", repos=["api"], workflow="dev-complex")
    (store.feature_dir("feat") / "api").mkdir(parents=True)

    service.execute_phase("feat")

    assert provider.requests[0].writable_dirs == [metadata_dir, common_dir]


def test_flow_service_execute_phase_supports_synthetic_provider_and_agent_filter(
    tmp_path: Path,
) -> None:
    ids = [
        "run_exec",
        "evt_started",
        "evt_provider",
        "evt_rendered",
        "evt_execution_started",
        "exec_phase",
        "evt_execution_completed",
        "evt_rendered_missing",
    ]
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    service.start(feature="feat", repos=[])

    summary = service.execute_phase("feat", provider_name="fake")

    assert summary.status == "completed"
    assert summary.executions[0].target.agent_id == "phase"
    assert summary.executions[0].target.synthetic is True
    with pytest.raises(ValueError, match="no rendered agent"):
        service.execute_phase("feat", agent_id="missing")


def test_flow_service_execute_phase_records_failed_execution_without_mutating_run(
    tmp_path: Path,
) -> None:
    ids = [
        "run_exec",
        "evt_started",
        "evt_provider",
        "evt_rendered",
        "evt_execution_started",
        "exec_failed",
        "evt_execution_failed",
    ]
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"failing": FailingFlowProvider()},
        default_provider="failing",
        clock=lambda: "2026-04-27T00:00:00Z",
        id_factory=lambda _prefix: ids.pop(0),
    )
    run = service.start(feature="feat", repos=[], provider_name="failing")

    summary = service.execute_phase("feat")

    assert summary.status == "failed"
    assert summary.executions[0].returncode == 9
    assert Path(summary.executions[0].paths["stderr"]).read_text(encoding="utf-8") == "failed\n"
    assert service.status("feat").status == run.status
    assert service.events("feat")[-1].type == "flow.phase.execution.failed"


def test_flow_service_execute_phase_respects_observer_policy(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "observer",
        _workflow_body(design_policy="observer"),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="observer")

    with pytest.raises(ValueError, match="Observer policy"):
        service.execute_phase("feat")

    assert service.execute_phase("feat", force_policy=True).status == "completed"


def test_flow_service_execute_phase_rejects_completed_runs_and_unknown_provider(
    tmp_path: Path,
) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[], phase=FlowPhase.REVIEW)
    service.advance("feat")

    with pytest.raises(ValueError, match="completed"):
        service.execute_phase("feat")
    with pytest.raises(ValueError, match="Unknown provider"):
        service.execute_phase("feat", provider_name="missing")


def test_flow_service_run_flow_stops_before_observer_execution(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "observer", _workflow_body(design_policy="observer"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="observer")

    summary = service.run_flow("feat")

    assert summary.status == "observer_stopped"
    assert summary.steps[0].status == "observer_stopped"
    assert summary.to_dict()["steps"][0]["policy"] == "observer"
    assert service.events("feat")[-1].type == "flow.run_loop.observer_stopped"


def test_flow_service_run_flow_stops_for_supervisor_policy(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "supervisor", _workflow_body(design_policy="supervisor"))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="supervisor")

    summary = service.run_flow("feat")

    assert summary.status == "waiting_human"
    assert summary.steps[0].status == "waiting_human"
    assert summary.steps[0].execution.status == "completed"
    assert summary.steps[0].artifacts.valid is True


def test_flow_service_run_flow_can_auto_advance_co_driver_when_explicit(
    tmp_path: Path,
) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    stopped = service.run_flow("feat", max_phases=1)
    advanced = service.run_flow("feat", max_phases=1, co_driver_auto_advance=True)

    assert stopped.status == "waiting_human"
    assert advanced.status == "max_phases"
    assert advanced.steps[0].status == "advanced"
    assert service.status("feat").current_phase == FlowPhase.IMPLEMENT


def test_flow_service_run_flow_autonomous_completes_single_phase(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "single", _single_phase_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="single")

    summary = service.run_flow("feat")

    assert summary.status == "completed"
    assert summary.run.status == FlowStatus.COMPLETED
    assert summary.steps[0].advanced_to == FlowPhase.DESIGN


def test_flow_service_run_flow_stops_on_missing_artifacts_execution_failure_and_gate(
    tmp_path: Path,
) -> None:
    _write_workflow(
        tmp_path,
        "missing-artifact",
        _workflow_body(design_policy="autonomous", design_required_artifact=True),
    )
    missing_service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "missing" / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    missing_service.start(feature="feat", repos=[], workflow="missing-artifact")

    missing = missing_service.run_flow("feat")

    assert missing.status == "missing_artifacts"
    assert missing.steps[0].artifacts.valid is False

    failing_service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "failing" / "_wt"),
        providers={"failing": FailingFlowProvider()},
        default_provider="failing",
    )
    failing_service.start(feature="feat", repos=[], provider_name="failing")

    failed = failing_service.run_flow("feat")

    assert failed.status == "failed"
    assert failed.steps[0].status == "execution_failed"

    _write_workflow(tmp_path, "approval", _single_phase_workflow(approval=True))
    gated_service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "gated" / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    gated_service.start(feature="feat", repos=[], workflow="approval")

    gated = gated_service.run_flow("feat")

    assert gated.status == "waiting_approval"
    assert gated.steps[0].status == "waiting_approval"


def test_flow_service_run_flow_validates_limits_and_unknown_provider(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    with pytest.raises(ValueError, match="max_phases"):
        service.run_flow("feat", max_phases=0)
    with pytest.raises(ValueError, match="Unknown provider"):
        service.run_flow("feat", provider_name="missing")


def test_flow_service_advances_default_phase_order_and_completes(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    service.start(feature="feat", repos=[], phase=FlowPhase.REVIEW)

    completed = service.advance("feat")

    assert completed.status == FlowStatus.COMPLETED
    assert completed.current_phase == FlowPhase.REVIEW
    assert completed.phase_history[-1]["to"] == ""
    assert service.events("feat")[-1].type == "flow.run.completed"
    with pytest.raises(ValueError, match="completed"):
        service.advance("feat")


def test_flow_service_rejects_unknown_transition_without_workflow(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[])

    with pytest.raises(ValueError, match="no workflow transition"):
        service.advance("feat", signal="blocked")


def test_flow_service_enforces_approval_before_workflow_advance(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="requires approval"):
        service.advance("feat")
    approved = service.approve("feat")
    advanced = service.advance("feat")

    assert run.current_phase == FlowPhase.DESIGN
    assert approved.approvals == {"design": "2026-04-27T00:00:00Z"}
    assert advanced.current_phase == FlowPhase.IMPLEMENT
    assert advanced.status == FlowStatus.RUNNING
    assert service.events("feat")[-2].type == "flow.phase.approved"
    assert service.events("feat")[-1].type == "flow.phase.advanced"


def test_flow_service_approve_rejects_non_gated_phase(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="does not require approval"):
        service.approve("feat")


def test_flow_service_approve_rejects_completed_runs(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "gated", _single_phase_workflow(approval=True))
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="gated")

    service.approve("feat")
    service.advance("feat")

    with pytest.raises(ValueError, match="completed"):
        service.approve("feat")


def test_flow_service_approve_rejects_non_current_phases(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="Can only approve current phase"):
        service.approve("feat", phase=FlowPhase.IMPLEMENT)


def test_flow_service_replan_clears_stale_phase_approvals(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    service.approve("feat")
    replanned = service.replan("feat", phase=FlowPhase.DESIGN, reason="new design")

    assert replanned.approvals == {}
    with pytest.raises(ValueError, match="requires approval"):
        service.advance("feat")


def test_flow_service_workflow_transition_signal_and_missing_signal(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "dev-complex", _workflow_body())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")

    with pytest.raises(ValueError, match="no transition"):
        service.advance("feat", signal="unknown")
    run = service.advance("feat", signal="complete")

    assert run.current_phase == FlowPhase.IMPLEMENT


def test_flow_service_block_and_replan_with_workflow_transition(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(implement_blocked_transition=True),
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
        clock=lambda: "2026-04-27T00:00:00Z",
    )
    service.start(feature="feat", repos=[], workflow="dev-complex")
    service.advance("feat")

    with pytest.raises(ValueError, match="reason is required"):
        service.block("feat", reason=" ")
    blocked = service.block("feat", reason="tests failed")
    with pytest.raises(ValueError, match="blocked"):
        service.advance("feat")
    replanned = service.replan("feat", reason="need simpler design")

    assert blocked.status == FlowStatus.BLOCKED
    assert blocked.blocked_reason == "tests failed"
    assert replanned.status == FlowStatus.RUNNING
    assert replanned.current_phase == FlowPhase.DESIGN
    assert replanned.blocked_reason == ""
    assert replanned.phase_history[-1]["on"] == "replan"
    assert [event.type for event in service.events("feat")][-2:] == [
        "flow.phase.blocked",
        "flow.phase.replanned",
    ]


def test_flow_service_replan_defaults_and_explicit_phase_without_workflow(tmp_path: Path) -> None:
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
    )
    service.start(feature="feat", repos=[], phase=FlowPhase.IMPLEMENT)

    blocked = service.block("feat", reason="blocked")
    replanned = service.replan("feat")
    explicit = service.replan("feat", phase=FlowPhase.VERIFY, reason="check")

    assert blocked.status == FlowStatus.BLOCKED
    assert replanned.current_phase == FlowPhase.DESIGN
    assert explicit.current_phase == FlowPhase.VERIFY


def test_flow_service_replan_rejects_phase_missing_from_workflow(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "single", _single_phase_workflow())
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="single")

    with pytest.raises(ValueError, match="has no phase 'explore'"):
        service.replan("feat", phase=FlowPhase.EXPLORE)


def test_flow_service_replan_stays_on_current_phase_when_no_design_phase(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "verify-only",
        """
version: 1
name: verify-only
work_type: dev
defaults:
  provider: fake
phases:
  - id: verify
    agents: []
""",
    )
    service = LocalFlowService(
        store=LocalFlowStore(tmp_path / "_wt"),
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    service.start(feature="feat", repos=[], workflow="verify-only")
    service.block("feat", reason="blocked")

    run = service.replan("feat")

    assert run.current_phase == FlowPhase.VERIFY
    assert run.phase_history[-1]["reason"] == "start"


def test_flow_service_rejects_invalid_stored_workflow_metadata(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    _write_workflow(
        tmp_path,
        "dev-complex",
        _workflow_body(design_requires_approval=True),
    )
    service = LocalFlowService(
        store=store,
        providers={"fake": FakeFlowProvider()},
        workflow_library=LocalWorkflowLibrary(tmp_path),
    )
    run = service.start(feature="feat", repos=[], workflow="dev-complex")
    store.write_run_json(run, "workflow.json", [])

    with pytest.raises(ValueError, match="Stored workflow metadata is invalid"):
        service.approve("feat")
