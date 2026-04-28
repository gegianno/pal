from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..models import FlowPhase, FlowPolicy
from .models import (
    WorkflowAgent,
    WorkflowDefaults,
    WorkflowPhase,
    WorkflowSpec,
    WorkflowTransition,
)


class WorkflowSpecError(ValueError):
    pass


_DEFAULT_POLICIES = {
    FlowPhase.EXPLORE.value: FlowPolicy.AUTONOMOUS,
    FlowPhase.DESIGN.value: FlowPolicy.AUTONOMOUS,
    FlowPhase.IMPLEMENT.value: FlowPolicy.AUTONOMOUS,
    FlowPhase.VERIFY.value: FlowPolicy.SUPERVISOR,
    FlowPhase.REVIEW.value: FlowPolicy.OBSERVER,
    FlowPhase.SHIP.value: FlowPolicy.SUPERVISOR,
}


class LocalWorkflowLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def flow_dir(self) -> Path:
        return self.root / ".pal" / "flows"

    def list_paths(self) -> list[Path]:
        if not self.flow_dir.exists():
            return []
        return sorted([*self.flow_dir.glob("*.yaml"), *self.flow_dir.glob("*.yml")])

    def resolve_path(self, workflow: str | Path) -> Path:
        candidate = Path(workflow)
        if candidate.suffix in {".yaml", ".yml"}:
            return candidate if candidate.is_absolute() else self.root / candidate
        for suffix in (".yaml", ".yml"):
            path = self.flow_dir / f"{workflow}{suffix}"
            if path.exists():
                return path
        return self.flow_dir / f"{workflow}.yaml"

    def load(self, workflow: str | Path) -> WorkflowSpec:
        path = self.resolve_path(workflow)
        if not path.exists():
            raise WorkflowSpecError(f"Workflow spec not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise WorkflowSpecError(f"Invalid YAML in {path}: {exc}") from exc
        if raw is None:
            raise WorkflowSpecError(f"Workflow spec is empty: {path}")
        if not isinstance(raw, dict):
            raise WorkflowSpecError(f"Workflow spec must be a mapping: {path}")
        return _parse_spec(raw, path)


def _parse_spec(raw: dict[str, Any], path: Path) -> WorkflowSpec:
    version = _required_int(raw, "version", path)
    if version != 1:
        raise WorkflowSpecError(f"{path}: unsupported workflow spec version {version}")

    name = _required_str(raw, "name", path)
    work_type = _required_str(raw, "work_type", path)
    description = _optional_str(raw, "description")
    mode = _optional_str(raw, "mode") or "complex"
    repos = _str_list(raw.get("repos", []), "repos", path)
    defaults = _parse_defaults(_mapping(raw.get("defaults", {}), "defaults", path), path)
    phases = _parse_phases(_required_list(raw, "phases", path), defaults, path)

    return WorkflowSpec(
        version=version,
        name=name,
        work_type=work_type,
        description=description,
        mode=mode,
        repos=repos,
        defaults=defaults,
        phases=phases,
        path=path,
    )


def _parse_defaults(raw: dict[str, Any], path: Path) -> WorkflowDefaults:
    policy = raw.get("policy")
    return WorkflowDefaults(
        provider=_optional_str(raw, "provider"),
        policy=_parse_policy(str(policy), path) if policy else None,
    )


def _parse_phases(
    raw_phases: list[Any],
    defaults: WorkflowDefaults,
    path: Path,
) -> list[WorkflowPhase]:
    if not raw_phases:
        raise WorkflowSpecError(f"{path}: phases must not be empty")

    phases: list[WorkflowPhase] = []
    seen: set[str] = set()
    valid_ids = {phase.value for phase in FlowPhase}

    for index, raw_phase in enumerate(raw_phases):
        phase_data = _mapping(raw_phase, f"phases[{index}]", path)
        phase_id = _required_str(phase_data, "id", path)
        if phase_id not in valid_ids:
            raise WorkflowSpecError(f"{path}: unknown phase '{phase_id}'")
        if phase_id in seen:
            raise WorkflowSpecError(f"{path}: duplicate phase '{phase_id}'")
        seen.add(phase_id)

        policy_value = phase_data.get("policy") or defaults.policy or _DEFAULT_POLICIES[phase_id]
        phase = _parse_phase_id(phase_id, path)
        phases.append(
            WorkflowPhase(
                id=phase,
                policy=_parse_policy(policy_value, path),
                provider=_optional_str(phase_data, "provider"),
                agents=_parse_agents(phase_data.get("agents", []), defaults, phase_data, path),
                required_artifacts=_str_list(
                    phase_data.get("required_artifacts", []), "required_artifacts", path
                ),
                transitions=_parse_transitions(phase_data.get("transitions", []), path),
                requires_approval=bool(phase_data.get("requires_approval", False)),
            )
        )

    phase_ids = {phase.id for phase in phases}
    for phase in phases:
        for transition in phase.transitions:
            if transition.to not in phase_ids:
                raise WorkflowSpecError(
                    f"{path}: phase '{phase.id.value}' transitions to missing phase "
                    f"'{transition.to.value}'"
                )
    return phases


def _parse_agents(
    raw_agents: Any,
    defaults: WorkflowDefaults,
    phase_data: dict[str, Any],
    path: Path,
) -> list[WorkflowAgent]:
    agents: list[WorkflowAgent] = []
    for index, raw_agent in enumerate(_list(raw_agents, "agents", path)):
        agent_data = _mapping(raw_agent, f"agents[{index}]", path)
        provider = (
            _optional_str(agent_data, "provider")
            or _optional_str(phase_data, "provider")
            or defaults.provider
        )
        agents.append(
            WorkflowAgent(
                id=_required_str(agent_data, "id", path),
                role=_required_str(agent_data, "role", path),
                provider=provider,
                prompt=_optional_str(agent_data, "prompt"),
                produces=_str_list(agent_data.get("produces", []), "produces", path),
                requires=_str_list(agent_data.get("requires", []), "requires", path),
            )
        )
    return agents


def _parse_transitions(raw_transitions: Any, path: Path) -> list[WorkflowTransition]:
    transitions: list[WorkflowTransition] = []
    for index, raw_transition in enumerate(_list(raw_transitions, "transitions", path)):
        transition_data = _mapping(raw_transition, f"transitions[{index}]", path)
        transitions.append(
            WorkflowTransition(
                on=_required_str(_normalize_transition_keys(transition_data), "on", path),
                to=_parse_phase_id(_required_str(transition_data, "to", path), path),
            )
        )
    return transitions


def _normalize_transition_keys(raw: dict[str, Any]) -> dict[str, Any]:
    if "on" not in raw and True in raw:
        return {**raw, "on": raw[True]}
    return raw


def _parse_phase_id(value: str, path: Path) -> FlowPhase:
    try:
        return FlowPhase(value)
    except ValueError as exc:
        raise WorkflowSpecError(f"{path}: unknown phase '{value}'") from exc


def _parse_policy(value: str | FlowPolicy, path: Path) -> FlowPolicy:
    if isinstance(value, FlowPolicy):
        return value
    try:
        return FlowPolicy(value)
    except ValueError as exc:
        raise WorkflowSpecError(f"{path}: unknown policy '{value}'") from exc


def _mapping(value: Any, name: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowSpecError(f"{path}: {name} must be a mapping")
    return value


def _list(value: Any, name: str, path: Path) -> list[Any]:
    if not isinstance(value, list):
        raise WorkflowSpecError(f"{path}: {name} must be a list")
    return value


def _required_list(raw: dict[str, Any], key: str, path: Path) -> list[Any]:
    if key not in raw:
        raise WorkflowSpecError(f"{path}: missing required field '{key}'")
    return _list(raw[key], key, path)


def _required_int(raw: dict[str, Any], key: str, path: Path) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise WorkflowSpecError(f"{path}: field '{key}' must be an integer")
    return value


def _required_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = _optional_str(raw, key)
    if not value:
        raise WorkflowSpecError(f"{path}: missing required field '{key}'")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _str_list(value: Any, name: str, path: Path) -> list[str]:
    return [str(item).strip() for item in _list(value, name, path) if str(item).strip()]
