from __future__ import annotations

from pathlib import Path

from pal.config import CodexConfig
from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import CommandResult, ProviderLaunchRequest
from pal.flow.providers.codex import CodexFlowProvider


class FakeRunner:
    def __init__(self, executable: str | None = "/bin/codex") -> None:
        self.executable = executable
        self.calls: list[tuple[list[str], Path | None, int | None]] = []

    def which(self, executable: str) -> str | None:
        return self.executable if executable == "codex" else None

    def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201
        self.calls.append((command, cwd, timeout))
        if command[-1:] == ["--version"]:
            return CommandResult(returncode=0, stdout="codex-cli 1.2.3\n")
        if command[-2:] == ["login", "status"]:
            return CommandResult(returncode=0, stdout="Logged in using ChatGPT\n")
        return CommandResult(returncode=0, stdout='{"event":"done"}\n', stderr="")


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


def test_codex_preflight_installed_and_logged_in() -> None:
    runner = FakeRunner()
    preflight = CodexFlowProvider(runner).preflight()

    assert preflight.installed is True
    assert preflight.version == "codex-cli 1.2.3"
    assert preflight.auth.status == "available"
    assert preflight.auth.detail == "Logged in using ChatGPT"
    assert preflight.capabilities.to_dict()["json_output"] is True
    assert runner.calls[0][0] == ["/bin/codex", "--version"]
    assert runner.calls[1][0] == ["/bin/codex", "login", "status"]


def test_codex_preflight_missing_cli() -> None:
    preflight = CodexFlowProvider(FakeRunner(executable=None)).preflight()

    assert preflight.installed is False
    assert preflight.auth.status == "unavailable"
    assert preflight.auth.detail == "codex CLI not found"


def test_codex_preflight_records_nonzero_login_status() -> None:
    class LoginFailureRunner(FakeRunner):
        def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201
            self.calls.append((command, cwd, timeout))
            if command[-1:] == ["--version"]:
                return CommandResult(returncode=0, stdout="codex-cli 1.2.3\n")
            return CommandResult(returncode=1, stderr="Not logged in\n")

    preflight = CodexFlowProvider(LoginFailureRunner()).preflight()

    assert preflight.auth.status == "unknown"
    assert preflight.auth.detail == "Not logged in"
    assert preflight.notes == ["codex login status returned a non-zero exit code"]


def test_codex_start_and_headless_launch(tmp_path: Path) -> None:
    runner = FakeRunner()
    provider = CodexFlowProvider(
        runner,
        codex=CodexConfig(sandbox="workspace-write", add_dirs=["cache", ""]),
        agent_add_dirs=["/tmp/shared", "/tmp/shared"],
    )
    start = provider.start(_run())
    launch = provider.launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / ".pal" / "runs" / "run_1" / "latest",
        )
    )

    assert start.summary == "codex provider initialized run run_1"
    assert launch.status == "completed"
    assert launch.command == [
        "/bin/codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "workspace-write",
        "--add-dir",
        str(Path("/tmp/shared").resolve()),
        "--add-dir",
        str((tmp_path / "cache").resolve()),
        "--skip-git-repo-check",
        "--json",
        "Summarize",
    ]
    assert launch.stdout == '{"event":"done"}\n'


def test_codex_headless_command_respects_full_auto(tmp_path: Path) -> None:
    command = CodexFlowProvider(
        FakeRunner(),
        codex=CodexConfig(full_auto=True, add_dirs=["/tmp/codex"]),
    ).headless_command(tmp_path, "Run")

    assert "--full-auto" in command
    assert "--sandbox" not in command
    assert command[command.index("--add-dir") + 1] == str(Path("/tmp/codex").resolve())


def test_codex_headless_launch_failure_status(tmp_path: Path) -> None:
    class FailureRunner(FakeRunner):
        def run(self, command: list[str], *, cwd=None, timeout=None):  # noqa: ANN001, ANN201
            self.calls.append((command, cwd, timeout))
            return CommandResult(returncode=2, stderr="failed\n")

    launch = CodexFlowProvider(FailureRunner()).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.returncode == 2
