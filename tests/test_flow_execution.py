from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.execution import (
    PhaseExecutionRecord,
    build_execution_prompt,
    execution_status,
    phase_execution_targets,
    redact_command_prompt,
)
from pal.flow.models import FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.rendering import PhaseBrief, ResolvedPhaseAgent
from pal.flow.workflows.models import WorkflowAgent, WorkflowPhase, WorkflowSpec, WorkflowTools


def _run() -> FlowRun:
    return FlowRun(
        run_id="run_1",
        feature="feat",
        mode="complex",
        repos=["api"],
        current_phase=FlowPhase.DESIGN,
        status=FlowStatus.RUNNING,
        policies={"design": FlowPolicy.CO_DRIVER},
        artifact_root="/tmp/feat/.pal/artifacts",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )


def _brief(*, agents: list[ResolvedPhaseAgent] | None = None) -> PhaseBrief:
    return PhaseBrief(
        run=_run(),
        phase=FlowPhase.DESIGN,
        policy=FlowPolicy.CO_DRIVER,
        workflow=None,
        agents=list(agents or []),
        required_artifacts=["artifacts/design.md"],
        events=[],
        provider_guidance={"fake": "fake guidance"},
        rendered_at="2026-04-27T00:01:00Z",
        paths={"markdown": "/tmp/brief.md", "json": "/tmp/brief.json"},
    )


def _workflow(*, agents: list[WorkflowAgent]) -> WorkflowSpec:
    return WorkflowSpec(
        version=1,
        name="dev-complex",
        work_type="dev",
        phases=[
            WorkflowPhase(
                id=FlowPhase.DESIGN,
                policy=FlowPolicy.CO_DRIVER,
                agents=agents,
            )
        ],
        path=Path(".pal/flows/dev-complex.yaml"),
    )


def _agent(
    agent_id: str = "designer",
    *,
    tools: WorkflowTools | None = None,
) -> ResolvedPhaseAgent:
    return ResolvedPhaseAgent(
        id=agent_id,
        role="design specialist",
        provider="fake",
        prompt="Create the design artifact.",
        produces=["artifacts/design.md"],
        requires=["json_output"],
        tools=tools or WorkflowTools(required=["github_write"], optional=["linear_write"]),
    )


def _record(status: str = "completed", returncode: int = 0) -> PhaseExecutionRecord:
    target = phase_execution_targets(_brief(agents=[_agent()]))[0]
    return PhaseExecutionRecord(
        execution_id="exec_1",
        phase=FlowPhase.DESIGN,
        target=target,
        execution_mode="local_headless",
        status=status,
        returncode=returncode,
        command=["fake"],
        cwd=str(Path("/tmp/feat")),
        started_at="2026-04-27T00:01:00Z",
        ended_at="2026-04-27T00:02:00Z",
        paths={"manifest": "/tmp/manifest.json"},
    )


def test_phase_execution_targets_use_agents_and_agent_filter() -> None:
    brief = _brief(agents=[_agent(), _agent("verifier")])

    targets = phase_execution_targets(brief)
    filtered = phase_execution_targets(brief, agent_id="verifier")

    assert [target.agent_id for target in targets] == ["designer", "verifier"]
    assert filtered[0].agent_id == "verifier"
    assert filtered[0].provider == "fake"
    assert filtered[0].synthetic is False
    assert filtered[0].tools.required == ["github_write"]


def test_phase_execution_targets_fall_back_to_phase_provider() -> None:
    targets = phase_execution_targets(_brief())

    assert len(targets) == 1
    assert targets[0].agent_id == "phase"
    assert targets[0].provider == "fake"
    assert targets[0].synthetic is True


def test_phase_execution_targets_fall_back_when_workflow_phase_has_no_agents() -> None:
    brief = _brief()
    brief = PhaseBrief(
        run=brief.run,
        phase=brief.phase,
        policy=brief.policy,
        workflow=_workflow(agents=[]),
        agents=[],
        required_artifacts=brief.required_artifacts,
        events=brief.events,
        provider_guidance=brief.provider_guidance,
        rendered_at=brief.rendered_at,
        paths=brief.paths,
    )

    assert phase_execution_targets(brief)[0].agent_id == "phase"


def test_phase_execution_targets_reject_provider_filters_that_remove_configured_agents() -> None:
    brief = _brief()
    brief = PhaseBrief(
        run=brief.run,
        phase=brief.phase,
        policy=brief.policy,
        workflow=_workflow(
            agents=[
                WorkflowAgent(
                    id="designer",
                    role="design",
                    provider="codex",
                    prompt="Design.",
                )
            ]
        ),
        agents=[],
        required_artifacts=brief.required_artifacts,
        events=brief.events,
        provider_guidance=brief.provider_guidance,
        rendered_at=brief.rendered_at,
        paths=brief.paths,
    )

    with pytest.raises(ValueError, match="provider filtering"):
        phase_execution_targets(brief)


def test_phase_execution_targets_reject_missing_agent_filter() -> None:
    with pytest.raises(ValueError, match="no rendered agent"):
        phase_execution_targets(_brief(agents=[_agent()]), agent_id="missing")


def test_build_execution_prompt_includes_target_contract_and_brief() -> None:
    brief = _brief(agents=[_agent()])
    target = phase_execution_targets(brief)[0]

    prompt = build_execution_prompt(brief, target)
    expected_artifact_path = Path("/tmp/feat/.pal/artifacts/design.md").resolve()

    assert prompt.startswith("# pal flow execution request")
    assert "Agent ID: `designer`" in prompt
    assert "Create the design artifact." in prompt
    assert f"`artifacts/design.md` -> `{expected_artifact_path}`" in prompt
    assert "`json_output`" in prompt
    assert "## Tool Expectations" in prompt
    assert "Required tools:" in prompt
    assert "`github_write`" in prompt
    assert "Optional tools:" in prompt
    assert "`linear_write`" in prompt
    assert "# pal flow phase brief" in prompt


def test_build_execution_prompt_handles_target_without_artifact_lists() -> None:
    target = phase_execution_targets(_brief())[0]

    prompt = build_execution_prompt(_brief(), target)

    assert "No agent-specific produced artifacts configured." in prompt
    assert "No agent-specific requirements configured." in prompt
    assert "No agent-specific tool expectations configured." in prompt


def test_build_execution_prompt_handles_required_only_tool_expectations() -> None:
    target = phase_execution_targets(
        _brief(agents=[_agent(tools=WorkflowTools(required=["github_write"]))])
    )[0]

    prompt = build_execution_prompt(_brief(), target)

    assert "Required tools:" in prompt
    assert "`github_write`" in prompt
    assert "Optional tools:" not in prompt


def test_build_execution_prompt_handles_optional_only_tool_expectations() -> None:
    target = phase_execution_targets(
        _brief(agents=[_agent(tools=WorkflowTools(optional=["linear_write"]))])
    )[0]

    prompt = build_execution_prompt(_brief(), target)

    assert "Required tools:" not in prompt
    assert "Optional tools:" in prompt
    assert "`linear_write`" in prompt


def test_execution_status_requires_successful_records() -> None:
    assert execution_status([_record()]) == "completed"
    assert execution_status([_record(status="failed", returncode=1)]) == "failed"
    assert execution_status([]) == "failed"
    data = _record().to_dict()
    assert data["target"]["agent_id"] == "designer"
    assert data["target"]["tools"] == {
        "required": ["github_write"],
        "optional": ["linear_write"],
    }
    assert data["paths"]["manifest"] == "/tmp/manifest.json"


def test_redact_command_prompt_replaces_exact_prompt_arguments() -> None:
    assert redact_command_prompt(["codex", "exec", "secret prompt"], "secret prompt") == [
        "codex",
        "exec",
        "<prompt>",
    ]
    assert redact_command_prompt(["codex", "exec", "safe"], "") == ["codex", "exec", "safe"]
