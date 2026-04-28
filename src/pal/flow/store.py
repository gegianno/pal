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
