from __future__ import annotations

import json
from pathlib import Path

from .events import FlowEventLog
from .models import FlowEvent, FlowRun


class LocalFlowStore:
    def __init__(self, worktree_root: Path) -> None:
        self.worktree_root = worktree_root

    def feature_dir(self, feature: str) -> Path:
        return self.worktree_root / feature

    def pal_dir(self, feature: str) -> Path:
        return self.feature_dir(feature) / ".pal"

    def runs_dir(self, feature: str) -> Path:
        return self.pal_dir(feature) / "runs"

    def run_dir(self, feature: str, run_id: str) -> Path:
        return self.runs_dir(feature) / run_id

    def latest_path(self, feature: str) -> Path:
        return self.runs_dir(feature) / "latest"

    def state_path(self, feature: str, run_id: str) -> Path:
        return self.run_dir(feature, run_id) / "state.json"

    def events_path(self, feature: str, run_id: str) -> Path:
        return self.run_dir(feature, run_id) / "events.jsonl"

    def latest_output_dir(self, feature: str, run_id: str) -> Path:
        return self.run_dir(feature, run_id) / "latest"

    def provider_log_path(self, feature: str, run_id: str, provider: str) -> Path:
        return self.run_dir(feature, run_id) / "providers" / f"{provider}.jsonl"

    def phase_dir(self, feature: str, run_id: str, phase: str) -> Path:
        return self.run_dir(feature, run_id) / "phase" / phase

    def phase_execution_dir(
        self,
        feature: str,
        run_id: str,
        phase: str,
        execution_id: str,
    ) -> Path:
        return self.phase_dir(feature, run_id, phase) / "executions" / execution_id

    def create_run(self, run: FlowRun) -> None:
        run_dir = self.run_dir(run.feature, run.run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "providers").mkdir()
        (run_dir / "latest").mkdir()
        self.save_state(run)
        self.latest_path(run.feature).write_text(run.run_id, encoding="utf-8")

    def save_state(self, run: FlowRun) -> None:
        path = self.state_path(run.feature, run.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_run_json(self, run: FlowRun, filename: str, data: object) -> Path:
        path = self.run_dir(run.feature, run.run_id) / filename
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def read_run_json(self, feature: str, run_id: str, filename: str) -> object:
        path = self.run_dir(feature, run_id) / filename
        return json.loads(path.read_text(encoding="utf-8"))

    def write_latest_output(
        self,
        run: FlowRun,
        *,
        provider: str,
        stdout: str,
        stderr: str,
    ) -> dict[str, str]:
        output_dir = self.latest_output_dir(run.feature, run.run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = output_dir / "stdout.log"
        stderr_path = output_dir / "stderr.log"
        provider_path = self.provider_log_path(run.feature, run.run_id, provider)
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        provider_path.write_text(stdout, encoding="utf-8")
        return {
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "provider_log": str(provider_path),
        }

    def write_phase_brief(
        self,
        run: FlowRun,
        *,
        phase: str,
        markdown: str,
        data: dict[str, object],
    ) -> dict[str, str]:
        phase_dir = self.phase_dir(run.feature, run.run_id, phase)
        phase_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = phase_dir / "brief.md"
        json_path = phase_dir / "brief.json"
        paths = {"markdown": str(markdown_path), "json": str(json_path)}
        data_with_paths = {**data, "paths": paths}
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(data_with_paths, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return paths

    def write_phase_execution(
        self,
        run: FlowRun,
        *,
        phase: str,
        execution_id: str,
        prompt: str,
        stdout: str,
        stderr: str,
        manifest: dict[str, object],
    ) -> dict[str, str]:
        execution_dir = self.phase_execution_dir(run.feature, run.run_id, phase, execution_id)
        execution_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = execution_dir / "prompt.md"
        stdout_path = execution_dir / "stdout.log"
        stderr_path = execution_dir / "stderr.log"
        manifest_path = execution_dir / "manifest.json"
        paths = {
            "prompt": str(prompt_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "manifest": str(manifest_path),
        }
        prompt_path.write_text(prompt, encoding="utf-8")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        manifest_path.write_text(
            json.dumps({**manifest, "paths": paths}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return paths

    def load_state(self, feature: str, run_id: str) -> FlowRun:
        data = json.loads(self.state_path(feature, run_id).read_text(encoding="utf-8"))
        return FlowRun.from_dict(data)

    def latest_run_id(self, feature: str) -> str:
        path = self.latest_path(feature)
        if not path.exists():
            raise FileNotFoundError(f"No flow runs found for feature '{feature}'.")
        return path.read_text(encoding="utf-8").strip()

    def resolve_run_id(self, feature: str, run_id: str | None) -> str:
        if run_id:
            return run_id
        return self.latest_run_id(feature)

    def load_run(self, feature: str, run_id: str | None = None) -> FlowRun:
        resolved_run_id = self.resolve_run_id(feature, run_id)
        return self.load_state(feature, resolved_run_id)

    def append_event(self, feature: str, run_id: str, event: FlowEvent) -> None:
        FlowEventLog(self.events_path(feature, run_id)).append(event)

    def read_events(self, feature: str, run_id: str | None = None) -> list[FlowEvent]:
        resolved_run_id = self.resolve_run_id(feature, run_id)
        return FlowEventLog(self.events_path(feature, resolved_run_id)).read()
