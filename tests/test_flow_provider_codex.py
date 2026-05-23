from __future__ import annotations

from pathlib import Path

from pal.config import CodexConfig
from pal.flow.models import FlowPhase, FlowRun, FlowStatus
from pal.flow.providers.base import CommandResult, ProviderLaunchRequest
from pal.flow.providers.codex import CodexFlowProvider


class FakeRunner:
    def __init__(self, executable: str | None = "/bin/codex") -> None:
        self.executable = executable
        self.calls: list[tuple[list[str], Path | None, int | None, str | None]] = []

    def which(self, executable: str) -> str | None:
        return self.executable if executable == "codex" else None

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
                return CommandResult(returncode=0, stdout="codex-cli 1.2.3\n")
            return CommandResult(returncode=1, stderr="Not logged in\n")

    preflight = CodexFlowProvider(LoginFailureRunner()).preflight()

    assert preflight.auth.status == "unknown"
    assert preflight.auth.detail == "Not logged in"
    assert preflight.notes == ["codex login status returned a non-zero exit code"]


def test_codex_preflight_reports_inaccessible_provider_state() -> None:
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
            if command[-1:] == ["--version"]:
                return CommandResult(returncode=0, stdout="codex-cli 1.2.3\n")
            return CommandResult(
                returncode=1,
                stderr=(
                    "Fatal error: Codex cannot access session files at "
                    "/Users/me/.codex/sessions (permission denied)\n"
                ),
            )

    preflight = CodexFlowProvider(StateFailureRunner()).preflight()

    assert preflight.auth.status == "unavailable"
    assert preflight.auth.detail == "Codex provider state is not accessible."
    assert preflight.notes == ["codex provider state is inaccessible"]


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
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(tmp_path),
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--add-dir",
        str(Path("/tmp/shared").resolve()),
        "--add-dir",
        str((tmp_path / "cache").resolve()),
        "--skip-git-repo-check",
        "--json",
        "-",
    ]
    assert runner.calls[-1][3] == "Summarize"
    assert launch.stdout == '{"event":"done"}\n'
    assert launch.diagnostics["executable"] == "/bin/codex"
    assert launch.diagnostics["prompt_chars"] == len("Summarize")
    assert launch.diagnostics["prompt_transport"] == "stdin"
    assert launch.diagnostics["headless_ephemeral"] is True
    assert launch.diagnostics["error"] == ""


def test_codex_headless_command_respects_full_auto(tmp_path: Path) -> None:
    command = CodexFlowProvider(
        FakeRunner(),
        codex=CodexConfig(full_auto=True, add_dirs=["/tmp/codex"]),
    ).headless_command(tmp_path, "Run")

    assert "--full-auto" in command
    assert "--ephemeral" in command
    assert "--ask-for-approval" not in command
    assert "--sandbox" not in command
    assert command[command.index("--add-dir") + 1] == str(Path("/tmp/codex").resolve())
    assert "Run" not in command
    assert command[-1] == "-"


def test_codex_headless_command_uses_configured_headless_approval(tmp_path: Path) -> None:
    command = CodexFlowProvider(
        FakeRunner(),
        codex=CodexConfig(headless_approval="on-failure"),
    ).headless_command(tmp_path, "Run")

    assert command[:4] == ["/bin/codex", "--ask-for-approval", "on-failure", "exec"]


def test_codex_headless_command_can_ignore_user_config(tmp_path: Path) -> None:
    command = CodexFlowProvider(
        FakeRunner(),
        codex=CodexConfig(headless_ignore_user_config=True),
    ).headless_command(tmp_path, "Run")

    assert "--ignore-user-config" in command
    assert command.index("--ignore-user-config") > command.index("exec")


def test_codex_headless_command_can_disable_ephemeral(tmp_path: Path) -> None:
    command = CodexFlowProvider(
        FakeRunner(),
        codex=CodexConfig(headless_ephemeral=False),
    ).headless_command(tmp_path, "Run")

    assert "--ephemeral" not in command


def test_codex_headless_launch_failure_status(tmp_path: Path) -> None:
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


def test_codex_headless_launch_reports_inaccessible_provider_state(tmp_path: Path) -> None:
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
                stderr=(
                    "Fatal error: Codex cannot access session files at "
                    "/Users/me/.codex/sessions (permission denied)\n"
                ),
            )

    launch = CodexFlowProvider(StateFailureRunner()).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.diagnostics["error"] == "provider_state_inaccessible"
    assert "Codex provider state is not accessible" in launch.stderr
    assert "Run pal from a process that can access CODEX_HOME or ~/.codex" in launch.stderr
    assert "Original provider stderr" in launch.stderr


def test_codex_headless_launch_reports_missing_cli(tmp_path: Path) -> None:
    launch = CodexFlowProvider(FakeRunner(executable=None)).launch_headless(
        ProviderLaunchRequest(
            run=_run(),
            workspace_dir=tmp_path,
            prompt="Summarize",
            output_dir=tmp_path / "latest",
        )
    )

    assert launch.status == "failed"
    assert launch.returncode == 127
    assert "codex CLI not found" in launch.stderr
    assert launch.diagnostics["error"] == "missing_executable"
