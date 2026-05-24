from __future__ import annotations

from pathlib import Path

import pytest

from pal.config import ClaudeConfig
from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import CommandResult, ProviderLaunchRequest
from pal.flow.providers.claude import ClaudeFlowProvider


class FakeRunner:
    def __init__(self, executable: str | None = "/bin/claude") -> None:
        self.executable = executable
        self.calls: list[tuple[list[str], Path | None, int | None, str | None]] = []

    def which(self, executable: str) -> str | None:
        return self.executable if executable == "claude" else None

    def run(  # noqa: ANN201
        self,
        command: list[str],
        *,
        cwd=None,  # noqa: ANN001
        timeout=None,  # noqa: ANN001
        input_text=None,  # noqa: ANN001
    ):
        self.calls.append((command, cwd, timeout, input_text))
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
    git_metadata_dir = tmp_path / ".git" / "worktrees" / "repo"
    git_metadata_dir.mkdir(parents=True)
    provider = ClaudeFlowProvider(
        runner,
        claude=ClaudeConfig(
            permission_mode="acceptEdits",
            model="sonnet",
            add_dirs=["cache", ""],
            extra_args=["--verbose"],
        ),
        agent_add_dirs=["/tmp/shared", "/tmp/shared"],
    )
    start = provider.start(_run())
    launch = provider.launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / ".pal" / "runs" / "run_1" / "latest",
            writable_dirs=[git_metadata_dir],
        )
    )

    assert start.summary == "claude provider initialized run run_1"
    assert launch.status == "completed"
    assert launch.command == [
        "/bin/claude",
        "--add-dir",
        str(Path("/tmp/shared").resolve()),
        "--add-dir",
        str((tmp_path / "cache").resolve()),
        "--add-dir",
        str(git_metadata_dir.resolve()),
        "-p",
        "--verbose",
        "--model",
        "sonnet",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "stream-json",
        "--input-format",
        "text",
    ]
    assert runner.calls[-1][1] == tmp_path
    assert runner.calls[-1][3] == "Summarize"
    assert launch.diagnostics["executable"] == "/bin/claude"
    assert launch.diagnostics["prompt_chars"] == len("Summarize")
    assert launch.diagnostics["prompt_transport"] == "stdin"
    assert launch.diagnostics["writable_dirs"] == [str(git_metadata_dir)]
    assert launch.diagnostics["error"] == ""


def test_claude_headless_command_respects_explicit_extra_args(tmp_path: Path) -> None:
    command = ClaudeFlowProvider(
        FakeRunner(),
        claude=ClaudeConfig(
            permission_mode="acceptEdits",
            model="sonnet",
            extra_args=[
                "--model=opus",
                "--permission-mode",
                "plan",
                "--output-format=json",
                "--input-format=stream-json",
            ],
        ),
    ).headless_command(tmp_path, "Run")

    assert "--model=opus" in command
    assert "sonnet" not in command
    assert command.count("--permission-mode") == 1
    assert "--output-format=json" in command
    assert "--output-format" not in command
    assert "--input-format=stream-json" in command
    assert "Run" not in command


def test_claude_headless_command_allows_blank_permission_mode(tmp_path: Path) -> None:
    command = ClaudeFlowProvider(
        FakeRunner(),
        claude=ClaudeConfig(permission_mode=""),
    ).headless_command(tmp_path, "Run")

    assert "--permission-mode" not in command
    assert "--output-format" in command
    assert "--input-format" in command


def test_claude_headless_rejects_bypass_permissions_by_default(tmp_path: Path) -> None:
    provider = ClaudeFlowProvider(
        FakeRunner(),
        claude=ClaudeConfig(extra_args=["--permission-mode=bypassPermissions"]),
    )

    with pytest.raises(ValueError, match="Bypass permissions mode is disabled"):
        provider.headless_command(tmp_path, "Run")


def test_claude_headless_rejects_dangerous_skip_flag_by_default(tmp_path: Path) -> None:
    provider = ClaudeFlowProvider(
        FakeRunner(),
        claude=ClaudeConfig(extra_args=["--dangerously-skip-permissions"]),
    )

    with pytest.raises(ValueError, match="Bypass permissions are disabled"):
        provider.headless_command(tmp_path, "Run")


def test_claude_headless_can_allow_bypass_permissions(tmp_path: Path) -> None:
    command = ClaudeFlowProvider(
        FakeRunner(),
        claude=ClaudeConfig(
            extra_args=["--permission-mode=bypassPermissions"],
            allow_bypass_permissions=True,
        ),
    ).headless_command(tmp_path, "Run")

    assert "--permission-mode=bypassPermissions" in command


def test_claude_headless_launch_failure_status(tmp_path: Path) -> None:
    class FailureRunner(FakeRunner):
        def run(  # noqa: ANN201
            self,
            command: list[str],
            *,
            cwd=None,  # noqa: ANN001
            timeout=None,  # noqa: ANN001
            input_text=None,  # noqa: ANN001
        ):
            self.calls.append((command, cwd, timeout, input_text))
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


def test_claude_headless_launch_reports_inaccessible_provider_state(tmp_path: Path) -> None:
    class StateFailureRunner(FakeRunner):
        def run(  # noqa: ANN201
            self,
            command: list[str],
            *,
            cwd=None,  # noqa: ANN001
            timeout=None,  # noqa: ANN001
            input_text=None,  # noqa: ANN001
        ):
            self.calls.append((command, cwd, timeout, input_text))
            return CommandResult(
                returncode=1,
                stderr="EACCES: permission denied, open '/Users/me/.claude/state.json'\n",
            )

    launch = ClaudeFlowProvider(StateFailureRunner()).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.diagnostics["error"] == "provider_state_inaccessible"
    assert "Claude Code provider state is not accessible" in launch.stderr
    assert "Run pal from a process that can access Claude Code's logged-in state directory" in (
        launch.stderr
    )


def test_claude_headless_launch_reports_missing_cli(tmp_path: Path) -> None:
    launch = ClaudeFlowProvider(FakeRunner(executable=None)).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.returncode == 127
    assert "claude CLI not found" in launch.stderr
    assert launch.diagnostics["error"] == "missing_executable"
