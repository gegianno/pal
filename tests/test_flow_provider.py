from __future__ import annotations

from pathlib import Path

from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import ProviderLaunchRequest
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


def test_fake_provider_capabilities_preflight_and_headless_launch(tmp_path: Path) -> None:
    provider = FakeFlowProvider()
    run = FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=[],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies=default_policies(),
        artifact_root=str(tmp_path / ".pal" / "artifacts"),
        created_at="now",
        updated_at="now",
    )

    assert provider.capabilities().to_dict()["provider"] == "fake"
    assert provider.preflight().to_dict()["auth"]["status"] == "available"
    result = provider.launch_headless(
        ProviderLaunchRequest(
            run=run,
            workspace_dir=tmp_path,
            prompt="hello",
            output_dir=tmp_path / ".pal" / "runs" / "run_1" / "latest",
        )
    )

    assert result.status == "completed"
    assert result.command == ["fake-flow-provider", "hello"]
    session = result.to_session_dict(
        session_id="session_1",
        started_at="start",
        ended_at="end",
    )
    assert session["session_id"] == "session_1"
    assert session["diagnostics"]["prompt_chars"] == len("hello")
