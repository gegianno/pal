from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.execution import (
    PhaseExecutionRecord,
    build_execution_prompt,
    execution_status,
    phase_execution_targets,
)
from pal.flow.models import FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.rendering import PhaseBrief, ResolvedPhaseAgent
from pal.flow.workflows.models import WorkflowAgent, WorkflowPhase, WorkflowSpec


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


def _agent(agent_id: str = "designer") -> ResolvedPhaseAgent:
    return ResolvedPhaseAgent(
        id=agent_id,
        role="design specialist",
        provider="fake",
        prompt="Create the design artifact.",
        produces=["artifacts/design.md"],
        requires=["json_output"],
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

    assert prompt.startswith("# pal flow execution request")
    assert "Agent ID: `designer`" in prompt
    assert "Create the design artifact." in prompt
    assert "`artifacts/design.md`" in prompt
    assert "`json_output`" in prompt
    assert "# pal flow phase brief" in prompt


def test_build_execution_prompt_handles_target_without_artifact_lists() -> None:
    target = phase_execution_targets(_brief())[0]

    prompt = build_execution_prompt(_brief(), target)

    assert "No agent-specific produced artifacts configured." in prompt
    assert "No agent-specific requirements configured." in prompt


def test_execution_status_requires_successful_records() -> None:
    assert execution_status([_record()]) == "completed"
    assert execution_status([_record(status="failed", returncode=1)]) == "failed"
    assert execution_status([]) == "failed"
    data = _record().to_dict()
    assert data["target"]["agent_id"] == "designer"
    assert data["paths"]["manifest"] == "/tmp/manifest.json"
