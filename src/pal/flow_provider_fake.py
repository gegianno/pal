from __future__ import annotations

from .flow_models import FlowRun
from .flow_provider import ProviderResult


class FakeFlowProvider:
    name = "fake"

    def start(self, run: FlowRun) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            summary=f"fake provider initialized run {run.run_id}",
            payload={"feature": run.feature, "mode": run.mode, "repos": list(run.repos)},
        )
