from __future__ import annotations

from pathlib import Path

from ..models import FlowRun
from .base import (
    ProviderAuthProfile,
    ProviderCapabilities,
    ProviderLaunchRequest,
    ProviderLaunchResult,
    ProviderPreflight,
    ProviderResult,
)
from .command import LocalCommandRunner


class ClaudeFlowProvider:
    name = "claude"

    def __init__(self, runner: LocalCommandRunner | None = None) -> None:
        self.runner = runner or LocalCommandRunner()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            execution_modes=["local_interactive", "local_headless"],
            resume=True,
            attach=True,
            json_output=True,
            stream_output=True,
            hooks=True,
            skills=True,
            native_subagents=True,
            mcp=True,
            sandbox_controls=True,
        )

    def preflight(self) -> ProviderPreflight:
        executable = self.runner.which("claude") or ""
        installed = bool(executable)
        version = ""
        auth = ProviderAuthProfile(
            provider=self.name,
            auth_mode="existing_session",
            status="unavailable" if not installed else "unknown",
            detail=(
                "claude CLI not found"
                if not installed
                else "claude CLI has no safe noninteractive auth-status command"
            ),
        )

        if installed:
            version_result = self.runner.run([executable, "--version"], timeout=10)
            version = (version_result.stdout or version_result.stderr).strip()

        return ProviderPreflight(
            provider=self.name,
            installed=installed,
            executable=executable,
            version=version,
            auth=auth,
            capabilities=self.capabilities(),
            notes=["auth status is unknown unless a later execution succeeds"] if installed else [],
        )

    def start(self, run: FlowRun) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            summary=f"claude provider initialized run {run.run_id}",
            payload={"feature": run.feature, "mode": run.mode, "repos": list(run.repos)},
        )

    def headless_command(self, workspace_dir: Path, prompt: str) -> list[str]:
        executable = self.runner.which("claude") or "claude"
        return [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "plan",
            prompt,
        ]

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        command = self.headless_command(request.workspace_dir, request.prompt)
        result = self.runner.run(command, cwd=request.workspace_dir)
        return ProviderLaunchResult(
            provider=self.name,
            execution_mode="local_headless",
            command=command,
            cwd=str(request.workspace_dir),
            status="completed" if result.returncode == 0 else "failed",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
