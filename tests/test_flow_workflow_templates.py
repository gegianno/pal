from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow.models import FlowPhase, FlowPolicy
from pal.flow.workflows.library import LocalWorkflowLibrary
from pal.flow.workflows.templates import (
    WorkflowTemplateError,
    list_workflow_templates,
    render_workflow_template,
    write_workflow_template,
)


def test_workflow_templates_are_listed_in_stable_order() -> None:
    templates = list_workflow_templates()

    assert [template.name for template in templates] == ["dev-complex", "dev-routine"]
    assert templates[0].mode == "complex"
    assert "specialist agents" in templates[0].description


def test_render_complex_workflow_template_is_valid_spec(tmp_path: Path) -> None:
    rendered = render_workflow_template(
        "dev-complex",
        workflow_name="my-dev-flow",
        provider="fake",
        work_type="dev",
        repos=["api", "web"],
    )
    path = tmp_path / ".pal" / "flows" / "my-dev-flow.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(rendered, encoding="utf-8")

    spec = LocalWorkflowLibrary(tmp_path).load("my-dev-flow")

    assert spec.name == "my-dev-flow"
    assert spec.defaults.provider == "fake"
    assert spec.work_type == "dev"
    assert spec.mode == "complex"
    assert spec.repos == ["api", "web"]
    assert [phase.id for phase in spec.phases] == [
        FlowPhase.EXPLORE,
        FlowPhase.DESIGN,
        FlowPhase.IMPLEMENT,
        FlowPhase.VERIFY,
        FlowPhase.PR,
        FlowPhase.REVIEW,
    ]
    assert spec.phases[0].policy == FlowPolicy.CO_DRIVER
    assert spec.phases[1].requires_approval is True
    assert spec.phase(FlowPhase.PR).requires_approval is True
    assert spec.phase(FlowPhase.REVIEW).policy == FlowPolicy.SUPERVISOR
    pr_agent = spec.phase(FlowPhase.PR).agents[0]
    verify_agent = spec.phase(FlowPhase.VERIFY).agents[0]
    review_agent = spec.phase(FlowPhase.REVIEW).agents[0]
    assert pr_agent.requires == ["local_headless", "json_output"]
    assert pr_agent.tools.required == ["github_write"]
    assert pr_agent.tools.optional == ["linear_write"]
    assert "native GitHub tools" in pr_agent.prompt
    assert "PR body must be structured" in pr_agent.prompt
    assert "browser or local UI checks" in verify_agent.prompt
    assert review_agent.tools.optional == ["github_read", "linear_read"]
    assert spec.transition_target(FlowPhase.IMPLEMENT, "blocked") == FlowPhase.DESIGN
    assert spec.transition_target(FlowPhase.VERIFY, "complete") == FlowPhase.PR
    assert spec.transition_target(FlowPhase.PR, "complete") == FlowPhase.REVIEW
    assert spec.transition_target(FlowPhase.REVIEW, "complete") is None
    assert len(spec.phases[0].agents) == 2
    assert len(spec.phases[2].agents) == 2
    for phase in spec.phases:
        produced = [artifact for agent in phase.agents for artifact in agent.produces]
        assert len(produced) == len(set(produced)), phase.id.value
        assert set(produced).issubset(set(phase.required_artifacts)), phase.id.value


def test_render_routine_workflow_template_uses_empty_repos() -> None:
    rendered = render_workflow_template(
        "dev-routine",
        workflow_name="routine",
        provider="claude",
        work_type="dev",
    )

    assert "repos: []" in rendered
    assert "provider: claude" in rendered
    assert "mode: routine" in rendered
    assert "  - id: pr" in rendered
    assert "  - id: review" in rendered
    assert "github_write" in rendered
    assert "linear_write" in rendered


def test_write_workflow_template_rejects_existing_unless_forced(tmp_path: Path) -> None:
    path = write_workflow_template(
        tmp_path,
        workflow_name="routine",
        template="dev-routine",
        provider="fake",
        work_type="dev",
    )

    with pytest.raises(WorkflowTemplateError, match="already exists"):
        write_workflow_template(
            tmp_path,
            workflow_name="routine",
            template="dev-routine",
            provider="fake",
            work_type="dev",
        )

    overwritten = write_workflow_template(
        tmp_path,
        workflow_name="routine",
        template="dev-complex",
        provider="fake",
        work_type="dev",
        repos=["api"],
        force=True,
    )

    assert overwritten == path
    assert LocalWorkflowLibrary(tmp_path).load("routine").mode == "complex"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"template": "missing"}, "Unknown workflow template"),
        ({"workflow_name": ""}, "workflow name must not be empty"),
        ({"workflow_name": "bad/name"}, "workflow name must start"),
        ({"workflow_name": "x" * 81}, "workflow name must be 80 characters or fewer"),
        ({"provider": "bad provider"}, "provider must start"),
        ({"work_type": " "}, "work type must not be empty"),
        ({"repos": ["bad repo"]}, "repo must start"),
    ],
)
def test_render_workflow_template_rejects_unsafe_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    options: dict[str, object] = {
        "template": "dev-routine",
        "workflow_name": "routine",
        "provider": "fake",
        "work_type": "dev",
        "repos": [],
    }
    options.update(kwargs)

    with pytest.raises(WorkflowTemplateError, match=message):
        render_workflow_template(**options)  # type: ignore[arg-type]
