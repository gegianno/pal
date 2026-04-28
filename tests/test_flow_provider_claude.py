from __future__ import annotations

from pathlib import Path

from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import CommandResult, ProviderLaunchRequest
from pal.flow.providers.claude import ClaudeFlowProvider


class FakeRunner:
    def __init__(self, executable: str | None = "/bin/claude") -> None:
        self.executable = executable
        self.calls: list[tuple[list[str], Path | None, int | None]] = []

    def which(self, executable: str) -> str | None:
        return self.executable if executable == "claude" else None

    def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201
        self.calls.append((command, cwd, timeout))
        if command[-1:] == ["--version"]:
            return CommandResult(returncode=0, stdout="2.1.34 (Claude Code)\n")
        return CommandResult(returncode=0, stdout='{"type":"result"}\n', stderr="")


def _run() -> FlowRun:
    return FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=[],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies={},
        artifact_root="/tmp/artifacts",
        created_at="now",
        updated_at="now",
    )


def test_claude_preflight_installed_with_unknown_auth() -> None:
    runner = FakeRunner()
    preflight = ClaudeFlowProvider(runner).preflight()

    assert preflight.installed is True
    assert preflight.version == "2.1.34 (Claude Code)"
    assert preflight.auth.status == "unknown"
    assert "no safe noninteractive auth-status command" in preflight.auth.detail
    assert preflight.capabilities.native_subagents is True
    assert preflight.notes == ["auth status is unknown unless a later execution succeeds"]
    assert runner.calls[0][0] == ["/bin/claude", "--version"]


def test_claude_preflight_missing_cli() -> None:
    preflight = ClaudeFlowProvider(FakeRunner(executable=None)).preflight()

    assert preflight.installed is False
    assert preflight.auth.status == "unavailable"
    assert preflight.auth.detail == "claude CLI not found"
    assert preflight.notes == []


def test_claude_start_and_headless_launch(tmp_path: Path) -> None:
    runner = FakeRunner()
    provider = ClaudeFlowProvider(runner)
    start = provider.start(_run())
    launch = provider.launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / ".pal" / "runs" / "run_1" / "latest",
        )
    )

    assert start.summary == "claude provider initialized run run_1"
    assert launch.status == "completed"
    assert launch.command == [
        "/bin/claude",
        "-p",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "plan",
        "Summarize",
    ]
    assert runner.calls[-1][1] == tmp_path


def test_claude_headless_launch_failure_status(tmp_path: Path) -> None:
    class FailureRunner(FakeRunner):
        def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201
            self.calls.append((command, cwd, timeout))
            return CommandResult(returncode=2, stderr="failed\n")

    launch = ClaudeFlowProvider(FailureRunner()).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.returncode == 2
