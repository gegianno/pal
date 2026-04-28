from __future__ import annotations

from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.service import default_policies


def test_fake_provider_returns_normalized_provider_result() -> None:
    run = FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=["api"],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies=default_policies(),
        artifact_root="/tmp/feat/.pal/artifacts",
        created_at="now",
        updated_at="now",
    )

    result = FakeFlowProvider().start(run)

    assert result.provider == "fake"
    assert result.summary == "fake provider initialized run run_1"
    assert result.payload == {"feature": "feat", "mode": "routine", "repos": ["api"]}
