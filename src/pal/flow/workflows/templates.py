from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from textwrap import dedent


class WorkflowTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    description: str
    mode: str
    body: str


_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_TOKEN_LENGTH = 80


_DEV_COMPLEX = WorkflowTemplate(
    name="dev-complex",
    description="Six-phase development workflow with specialist agents, gates, and replan loops.",
    mode="complex",
    body="""
version: 1
name: {workflow_name}
description: Multi-phase development flow with specialist agents and explicit human gates.
work_type: {work_type}
mode: complex
{repos}
defaults:
  provider: {provider}
phases:
  - id: explore
    policy: co-driver
    required_artifacts:
      - artifacts/explore.md
      - artifacts/risks.md
    transitions:
      - on: complete
        to: design
      - on: blocked
        to: design
    agents:
      - id: codebase-explorer
        role: codebase and dependency mapper
        prompt: Inspect the relevant repos, existing patterns, ownership boundaries, and tests.
        produces:
          - artifacts/explore.md
        requires:
          - local_headless
          - json_output
      - id: risk-mapper
        role: risk, migration, and unknowns analyst
        prompt: Identify failure modes, stale assumptions, rollout risks, and unresolved questions.
        produces:
          - artifacts/risks.md
        requires:
          - local_headless
          - json_output
  - id: design
    policy: supervisor
    requires_approval: true
    required_artifacts:
      - artifacts/design.md
    transitions:
      - on: complete
        to: implement
      - on: blocked
        to: explore
    agents:
      - id: solution-designer
        role: implementation architecture and task decomposition
        prompt: Convert exploration into a concrete design, invariants, test plan, and slices.
        produces:
          - artifacts/design.md
        requires:
          - local_headless
          - json_output
  - id: implement
    policy: co-driver
    required_artifacts:
      - artifacts/implementation.md
    transitions:
      - on: complete
        to: verify
      - on: blocked
        to: design
    agents:
      - id: implementer
        role: primary code-change worker
        prompt: Implement the approved slice, preserve repo conventions, and record changed files.
        produces:
          - artifacts/implementation.md
        requires:
          - local_headless
          - json_output
      - id: integration-guard
        role: integration and compatibility guard
        prompt: Check interfaces, migrations, cross-repo contracts, and backwards compatibility.
        produces:
          - artifacts/implementation.md
        requires:
          - local_headless
          - json_output
  - id: verify
    policy: supervisor
    required_artifacts:
      - artifacts/verification.md
    transitions:
      - on: complete
        to: review
      - on: blocked
        to: implement
    agents:
      - id: test-runner
        role: validation and test execution specialist
        prompt: Run targeted and full validation, capture exact commands, failures, and fixes.
        produces:
          - artifacts/verification.md
        requires:
          - local_headless
          - json_output
      - id: regression-hunter
        role: regression, edge-case, and coverage analyst
        prompt: Look for untested branches, behavioral regressions, and missing validation evidence.
        produces:
          - artifacts/verification.md
        requires:
          - local_headless
          - json_output
  - id: review
    policy: observer
    required_artifacts:
      - artifacts/review.md
    transitions:
      - on: complete
        to: ship
      - on: blocked
        to: implement
    agents:
      - id: code-reviewer
        role: reviewer focused on defects and maintainability
        prompt: Review the diff for correctness bugs, regressions, security issues, and test gaps.
        produces:
          - artifacts/review.md
        requires:
          - local_headless
          - json_output
      - id: docs-reviewer
        role: docs, changelog, and operator-readiness reviewer
        prompt: Check docs, upgrade notes, operational implications, and handoff clarity.
        produces:
          - artifacts/review.md
        requires:
          - local_headless
          - json_output
  - id: ship
    policy: supervisor
    requires_approval: true
    required_artifacts:
      - artifacts/ship.md
    transitions:
      - on: blocked
        to: verify
    agents:
      - id: shipper
        role: PR and release-readiness specialist
        prompt: Prepare the final PR summary, validation evidence, residual risks, and next steps.
        produces:
          - artifacts/ship.md
        requires:
          - local_headless
          - json_output
""",
)


_DEV_ROUTINE = WorkflowTemplate(
    name="dev-routine",
    description="Three-phase development workflow for small safe changes.",
    mode="routine",
    body="""
version: 1
name: {workflow_name}
description: Routine development flow for small, low-risk code changes.
work_type: {work_type}
mode: routine
{repos}
defaults:
  provider: {provider}
phases:
  - id: implement
    policy: co-driver
    required_artifacts:
      - artifacts/implementation.md
    transitions:
      - on: complete
        to: verify
      - on: blocked
        to: implement
    agents:
      - id: implementer
        role: code-change worker
        prompt: Implement the requested change and record the files changed.
        produces:
          - artifacts/implementation.md
        requires:
          - local_headless
          - json_output
  - id: verify
    policy: supervisor
    required_artifacts:
      - artifacts/verification.md
    transitions:
      - on: complete
        to: ship
      - on: blocked
        to: implement
    agents:
      - id: verifier
        role: validation specialist
        prompt: Run the relevant validation commands and capture exact evidence.
        produces:
          - artifacts/verification.md
        requires:
          - local_headless
          - json_output
  - id: ship
    policy: supervisor
    requires_approval: true
    required_artifacts:
      - artifacts/ship.md
    transitions:
      - on: blocked
        to: verify
    agents:
      - id: shipper
        role: PR readiness specialist
        prompt: Summarize the completed change, validation, risks, and next steps.
        produces:
          - artifacts/ship.md
        requires:
          - local_headless
          - json_output
""",
)


WORKFLOW_TEMPLATES = {
    _DEV_COMPLEX.name: _DEV_COMPLEX,
    _DEV_ROUTINE.name: _DEV_ROUTINE,
}


def list_workflow_templates() -> list[WorkflowTemplate]:
    return [WORKFLOW_TEMPLATES[name] for name in sorted(WORKFLOW_TEMPLATES)]


def render_workflow_template(
    template: str,
    *,
    workflow_name: str,
    provider: str,
    work_type: str,
    repos: list[str] | None = None,
) -> str:
    selected = _template_or_error(template)
    return (
        dedent(selected.body)
        .strip()
        .format(
            workflow_name=_safe_token(workflow_name, "workflow name"),
            work_type=_safe_token(work_type, "work type"),
            provider=_safe_token(provider, "provider"),
            repos=_repos_yaml(repos or []),
        )
        + "\n"
    )


def write_workflow_template(
    root: Path,
    *,
    workflow_name: str,
    template: str,
    provider: str,
    work_type: str,
    repos: list[str] | None = None,
    force: bool = False,
) -> Path:
    path = _workflow_path(root, workflow_name)
    if path.exists() and not force:
        raise WorkflowTemplateError(f"Workflow already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_workflow_template(
            template,
            workflow_name=workflow_name,
            provider=provider,
            work_type=work_type,
            repos=repos,
        ),
        encoding="utf-8",
    )
    return path


def _template_or_error(template: str) -> WorkflowTemplate:
    try:
        return WORKFLOW_TEMPLATES[template]
    except KeyError as exc:
        valid = ", ".join(sorted(WORKFLOW_TEMPLATES))
        raise WorkflowTemplateError(
            f"Unknown workflow template '{template}'. Expected one of: {valid}"
        ) from exc


def _workflow_path(root: Path, workflow_name: str) -> Path:
    safe_name = _safe_token(workflow_name, "workflow name")
    return root / ".pal" / "flows" / f"{safe_name}.yaml"


def _safe_token(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise WorkflowTemplateError(f"{label} must not be empty.")
    if len(normalized) > _MAX_TOKEN_LENGTH:
        raise WorkflowTemplateError(f"{label} must be {_MAX_TOKEN_LENGTH} characters or fewer.")
    if not _SAFE_TOKEN_RE.fullmatch(normalized):
        raise WorkflowTemplateError(
            f"{label} must start with a letter or number and contain only letters, "
            "numbers, dots, underscores, or dashes."
        )
    return normalized


def _repos_yaml(repos: list[str]) -> str:
    safe_repos = [_safe_token(repo, "repo") for repo in repos]
    if not safe_repos:
        return "repos: []"
    return "repos:\n" + "\n".join(f"  - {repo}" for repo in safe_repos)
