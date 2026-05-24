from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.rendering import PhaseBrief, build_phase_brief, provider_guidance
from pal.flow.workflows.models import (
    WorkflowAgent,
    WorkflowDefaults,
    WorkflowPhase,
    WorkflowSpec,
    WorkflowTools,
)


def _run(phase: FlowPhase = FlowPhase.DESIGN) -> FlowRun:
    return FlowRun(
        run_id="run_1",
        feature="feat",
        mode="complex",
        repos=["api"],
        current_phase=phase,
        status=FlowStatus.RUNNING,
        policies={phase.value: FlowPolicy.SUPERVISOR},
        artifact_root="/tmp/feat/.pal/artifacts",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
        workflow_name="dev-complex",
        work_type="dev",
        approvals={"design": "2026-04-27T00:00:00Z"},
        approval_reasons={"design": "Accepted manual verification."},
        phase_history=[{"phase": phase.value}],
        request="Fix the select styling regression.",
    )


def _event(event_id: str = "evt_1") -> FlowEvent:
    return FlowEvent(
        id=event_id,
        run_id="run_1",
        type="flow.run.started",
        timestamp="2026-04-27T00:00:00Z",
        phase=FlowPhase.DESIGN,
        actor="pal",
        payload={"summary": "started"},
    )


def _workflow(agent: WorkflowAgent | None = None) -> WorkflowSpec:
    return WorkflowSpec(
        version=1,
        name="dev-complex",
        work_type="dev",
        path=Path(".pal/flows/dev-complex.yaml"),
        defaults=WorkflowDefaults(provider="codex"),
        phases=[
            WorkflowPhase(
                id=FlowPhase.DESIGN,
                policy=FlowPolicy.CO_DRIVER,
                provider="claude",
                agents=[
                    agent
                    or WorkflowAgent(
                        id="designer",
                        role="design specialist",
                        provider="",
                        prompt="Design the implementation.",
                        produces=["artifacts/design.md"],
                        requires=["json_output"],
                        tools=WorkflowTools(
                            required=["github_write"],
                            optional=["linear_write"],
                        ),
                    )
                ],
                required_artifacts=["artifacts/design.md"],
                requires_approval=True,
            )
        ],
    )


def test_build_phase_brief_resolves_agents_guidance_and_serializes() -> None:
    phase_less_event = FlowEvent(
        id="evt_no_phase",
        run_id="run_1",
        type="custom",
        timestamp="2026-04-27T00:02:00Z",
        phase=None,
        actor="test",
        payload={},
    )
    brief = build_phase_brief(
        run=_run(),
        workflow=_workflow(),
        events=[_event(), phase_less_event],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
        pal_command="/tmp/pal current",
    ).with_paths({"markdown": "/tmp/brief.md", "json": "/tmp/brief.json"})

    data = brief.to_dict()
    markdown = brief.to_markdown()
    expected_artifact_path = Path("/tmp/feat/.pal/artifacts/design.md").resolve()

    assert data["rendered_at"] == "2026-04-27T00:01:00Z"
    assert data["pal_command"] == "/tmp/pal current"
    assert data["phase"]["id"] == "design"
    assert data["phase"]["policy"] == "co-driver"
    assert data["phase"]["requires_approval"] is True
    assert data["agents"][0]["provider"] == "claude"
    assert data["agents"][0]["tools"] == {
        "required": ["github_write"],
        "optional": ["linear_write"],
    }
    assert data["provider_guidance"]["claude"].startswith("Use Claude Code")
    assert data["events"][0]["summary"] == "started"
    assert data["events"][1]["phase"] == ""
    assert data["paths"]["markdown"] == "/tmp/brief.md"
    assert data["run"]["request"] == "Fix the select styling regression."
    assert data["approval_reasons"] == {"design": "Accepted manual verification."}
    assert "# pal flow phase brief" in markdown
    assert "## User Request" in markdown
    assert "Fix the select styling regression." in markdown
    assert "`designer` (claude)" in markdown
    assert "## Artifact Path Rules" in markdown
    assert "Do not create a nested `artifacts/` directory" in markdown
    assert "## Tool Delegation" in markdown
    assert "Pal does not manage external connector auth" in markdown
    assert "GitHub, Linear, Slack" in markdown
    assert "Required tools: github_write" in markdown
    assert "Optional tools: linear_write" in markdown
    assert f"`artifacts/design.md` -> `{expected_artifact_path}`" in markdown
    assert "Approval reasons: `design: Accepted manual verification.`" in markdown
    assert "Pal command: `'/tmp/pal current'`" in markdown
    assert "`'/tmp/pal current' flow block`" in markdown
    assert "rather than another `pal` binary on PATH" in markdown
    assert "/tmp/feat/.pal/artifacts" in markdown


def test_build_phase_brief_filters_provider_guidance() -> None:
    brief = build_phase_brief(
        run=_run(),
        workflow=_workflow(),
        events=[],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
        provider_filter="codex",
    )

    assert brief.agents == []
    assert list(brief.provider_guidance) == ["codex"]
    assert "Use Codex" in brief.to_markdown()
    assert "No events recorded" in brief.to_markdown()


def test_build_phase_brief_handles_runs_without_workflows() -> None:
    run = FlowRun.from_dict(
        {
            **_run(FlowPhase.VERIFY).to_dict(),
            "workflow_name": "",
            "work_type": "",
            "policies": {"verify": "observer"},
            "request": "",
        }
    )

    brief = build_phase_brief(
        run=run,
        workflow=None,
        events=[],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
    )

    assert brief.policy == FlowPolicy.OBSERVER
    assert brief.required_artifacts == []
    assert brief.provider_guidance["fake"].startswith("No provider-specific")
    assert "No agents configured" in brief.to_markdown()
    assert "No required artifacts configured" in brief.to_markdown()
    assert "No user request recorded." in brief.to_markdown()


def test_build_phase_brief_renders_verification_status_contract() -> None:
    brief = build_phase_brief(
        run=_run(FlowPhase.VERIFY),
        workflow=None,
        events=[],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
    )
    markdown = brief.to_markdown()

    assert "## Verification Outcome Contract" in markdown
    assert "`passed`" in markdown
    assert "`blocked`" in markdown
    assert "supervisor approval with a reason" in markdown
    assert "`failed`" in markdown


def test_build_phase_brief_uses_workflow_default_provider_without_agents() -> None:
    workflow = WorkflowSpec(
        version=1,
        name="dev-empty",
        work_type="dev",
        path=Path(".pal/flows/dev-empty.yaml"),
        defaults=WorkflowDefaults(provider="codex"),
        phases=[
            WorkflowPhase(
                id=FlowPhase.DESIGN,
                policy=FlowPolicy.AUTONOMOUS,
                agents=[],
            )
        ],
    )

    brief = build_phase_brief(
        run=_run(),
        workflow=workflow,
        events=[],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
    )

    assert list(brief.provider_guidance) == ["codex"]


def test_build_phase_brief_renders_agent_without_requirements() -> None:
    brief = build_phase_brief(
        run=_run(),
        workflow=_workflow(
            WorkflowAgent(
                id="writer",
                role="artifact writer",
                provider="",
                prompt="Write the artifact.",
            )
        ),
        events=[],
        rendered_at="2026-04-27T00:01:00Z",
        default_provider="fake",
    )

    markdown = brief.to_markdown()

    assert "`writer` (claude)" in markdown
    assert "Requires:" not in markdown
    assert "Required tools:" not in markdown
    assert "Optional tools:" not in markdown


def test_build_phase_brief_rejects_agents_without_context_or_provider() -> None:
    with pytest.raises(ValueError, match="needs a role or prompt"):
        build_phase_brief(
            run=_run(),
            workflow=_workflow(WorkflowAgent(id="bad", role="", provider="fake", prompt="")),
            events=[],
            rendered_at="2026-04-27T00:01:00Z",
            default_provider="fake",
        )

    with pytest.raises(ValueError, match="needs a provider"):
        build_phase_brief(
            run=_run(),
            workflow=WorkflowSpec(
                version=1,
                name="no-provider",
                work_type="dev",
                path=Path("flow.yaml"),
                phases=[
                    WorkflowPhase(
                        id=FlowPhase.DESIGN,
                        policy=FlowPolicy.AUTONOMOUS,
                        agents=[
                            WorkflowAgent(
                                id="agent",
                                role="role",
                                provider="",
                                prompt="",
                            )
                        ],
                    )
                ],
            ),
            events=[],
            rendered_at="2026-04-27T00:01:00Z",
            default_provider="",
        )


def test_phase_brief_markdown_handles_empty_provider_guidance() -> None:
    brief = PhaseBrief(
        run=_run(),
        phase=FlowPhase.DESIGN,
        policy=FlowPolicy.SUPERVISOR,
        workflow=None,
        agents=[],
        required_artifacts=[],
        events=[],
        provider_guidance={},
        rendered_at="2026-04-27T00:01:00Z",
    )

    assert "No provider guidance rendered." in brief.to_markdown()


def test_provider_guidance_variants() -> None:
    assert provider_guidance("codex").startswith("Use Codex")
    assert provider_guidance("claude").startswith("Use Claude Code")
    assert provider_guidance("other").startswith("No provider-specific")
