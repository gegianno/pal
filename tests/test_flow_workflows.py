from __future__ import annotations

import re
from pathlib import Path

import pytest

from pal.flow.models import FlowPhase, FlowPolicy
from pal.flow.workflows.library import LocalWorkflowLibrary, WorkflowSpecError
from pal.flow.workflows.models import WorkflowDefaults, WorkflowValidationResult


def _write_workflow(root: Path, name: str, body: str) -> Path:
    path = root / ".pal" / "flows" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _valid_workflow(provider: str = "fake", requirement: str = "local_headless") -> str:
    return f"""
version: 1
name: dev-complex
work_type: dev
description: Complex development flow
mode: complex
repos:
  - api
defaults:
  provider: {provider}
  policy: supervisor
phases:
  - id: explore
    required_artifacts:
      - artifacts/explore.md
    transitions:
      - on: complete
        to: design
    agents:
      - id: explorer
        role: codebase exploration
        prompt: Inspect the relevant repos.
        produces:
          - artifacts/explore.md
        requires:
          - {requirement}
  - id: design
    policy: co-driver
    requires_approval: true
    agents: []
"""


def test_workflow_library_loads_spec_defaults_and_transitions(tmp_path: Path) -> None:
    path = _write_workflow(tmp_path, "dev-complex", _valid_workflow())
    library = LocalWorkflowLibrary(tmp_path)

    spec = library.load("dev-complex")

    assert library.flow_dir == tmp_path / ".pal" / "flows"
    assert library.list_paths() == [path]
    assert library.resolve_path("missing") == tmp_path / ".pal" / "flows" / "missing.yaml"
    assert library.resolve_path(Path(".pal/flows/dev-complex.yaml")) == path
    assert spec.version == 1
    assert spec.name == "dev-complex"
    assert spec.work_type == "dev"
    assert spec.description == "Complex development flow"
    assert spec.mode == "complex"
    assert spec.repos == ["api"]
    assert spec.initial_phase == FlowPhase.EXPLORE
    assert spec.policies_by_phase() == {
        "explore": FlowPolicy.SUPERVISOR,
        "design": FlowPolicy.CO_DRIVER,
    }
    assert spec.referenced_providers("fake") == {"fake"}
    assert spec.phases[0].transitions[0].to == FlowPhase.DESIGN
    assert spec.phases[0].agents[0].to_dict()["produces"] == ["artifacts/explore.md"]
    assert spec.phases[1].requires_approval is True
    assert spec.to_run_dict()["path"] == str(path)


def test_workflow_library_loads_yml_files_and_service_default_policy(tmp_path: Path) -> None:
    path = tmp_path / ".pal" / "flows" / "routine.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
version: 1
name: routine
work_type: dev
phases:
  - id: verify
    provider: fake
    agents:
      - id: verifier
        role: verification
""",
        encoding="utf-8",
    )

    spec = LocalWorkflowLibrary(tmp_path).load("routine")

    assert LocalWorkflowLibrary(tmp_path).list_paths() == [path]
    assert spec.defaults.to_dict() == {}
    assert spec.phases[0].policy == FlowPolicy.SUPERVISOR
    assert spec.phases[0].provider == "fake"
    assert spec.phases[0].agents[0].provider == "fake"
    assert spec.phases[0].agents[0].prompt == ""
    assert spec.referenced_providers("codex") == {"codex", "fake"}


def test_workflow_library_converts_null_optional_strings(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "nulls",
        """
version: 1
name: nulls
work_type: dev
description:
defaults:
  provider:
phases:
  - id: explore
    provider:
    agents:
      - id: explorer
        role: exploration
        prompt:
""",
    )

    spec = LocalWorkflowLibrary(tmp_path).load("nulls")

    assert spec.description == ""
    assert spec.defaults.provider == ""
    assert spec.phases[0].provider == ""
    assert spec.phases[0].agents[0].prompt == ""


def test_workflow_library_accepts_absolute_paths_and_empty_directory(tmp_path: Path) -> None:
    path = _write_workflow(tmp_path, "absolute", _valid_workflow())
    empty = tmp_path / "empty"
    empty.mkdir()

    assert LocalWorkflowLibrary(empty).list_paths() == []
    assert LocalWorkflowLibrary(tmp_path).load(path).name == "dev-complex"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "Workflow spec is empty"),
        ("[]", "must be a mapping"),
        ("version: 2\nname: bad\nwork_type: dev\nphases: []\n", "unsupported"),
        ("version: one\nname: bad\nwork_type: dev\nphases: []\n", "must be an integer"),
        ("version: 1\nwork_type: dev\nphases: []\n", "missing required field 'name'"),
        ("version: 1\nname: bad\nwork_type: dev\n", "missing required field 'phases'"),
        ("version: 1\nname: bad\nwork_type: dev\nphases: nope\n", "phases must be a list"),
        ("version: 1\nname: bad\nwork_type: dev\nphases: []\n", "phases must not be empty"),
        (
            "version: 1\nname: bad\nwork_type: dev\ndefaults: []\nphases: []\n",
            "defaults must be a mapping",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\ndefaults:\n  policy: nope\nphases: []\n",
            "unknown policy",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nrepos: nope\nphases: []\n",
            "repos must be a list",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - nope\n",
            "phases[0] must be a mapping",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: unknown\n",
            "unknown phase",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n  - id: explore\n",
            "duplicate phase",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    policy: nope\n",
            "unknown policy",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    agents: nope\n",
            "agents must be a list",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "agents:\n      - nope\n",
            "agents[0] must be a mapping",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "agents:\n      - role: missing id\n",
            "missing required field 'id'",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "agents:\n      - id: missing-role\n",
            "missing required field 'role'",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "required_artifacts: nope\n",
            "required_artifacts must be a list",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "transitions: nope\n",
            "transitions must be a list",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "transitions:\n      - nope\n",
            "transitions[0] must be a mapping",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "transitions:\n      - to: design\n",
            "missing required field 'on'",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "transitions:\n      - on: complete\n        to: missing\n",
            "unknown phase",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "transitions:\n      - on: complete\n        to: design\n",
            "transitions to missing phase",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "agents:\n      - id: a\n        role: r\n        produces: nope\n",
            "produces must be a list",
        ),
        (
            "version: 1\nname: bad\nwork_type: dev\nphases:\n  - id: explore\n    "
            "agents:\n      - id: a\n        role: r\n        requires: nope\n",
            "requires must be a list",
        ),
    ],
)
def test_workflow_library_reports_invalid_specs(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    _write_workflow(tmp_path, "bad", body)

    with pytest.raises(WorkflowSpecError, match=re.escape(message)):
        LocalWorkflowLibrary(tmp_path).load("bad")


def test_workflow_library_reports_missing_and_invalid_yaml(tmp_path: Path) -> None:
    library = LocalWorkflowLibrary(tmp_path)

    with pytest.raises(WorkflowSpecError, match="not found"):
        library.load("missing")

    _write_workflow(tmp_path, "broken", "version: [")
    with pytest.raises(WorkflowSpecError, match="Invalid YAML"):
        library.load("broken")


def test_workflow_validation_result_valid_property() -> None:
    assert WorkflowValidationResult(Path("flow.yaml"), "flow", "dev").valid is True
    assert WorkflowValidationResult(Path("flow.yaml"), "flow", "dev", errors=["bad"]).valid is False


def test_workflow_defaults_with_provider_only() -> None:
    assert WorkflowDefaults(provider="codex").to_dict() == {"provider": "codex"}
