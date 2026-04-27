from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .flow_models import FlowRun


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


class FakeFlowProvider:
    name = "fake"

    def start(self, run: FlowRun) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            summary=f"fake provider initialized run {run.run_id}",
            payload={"feature": run.feature, "mode": run.mode, "repos": list(run.repos)},
        )
