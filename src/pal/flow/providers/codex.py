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


class CodexFlowProvider:
    name = "codex"

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
            hooks=False,
            skills=True,
            native_subagents=False,
            mcp=True,
            sandbox_controls=True,
            fallbacks={"native_subagents": "prompt_bundle"},
        )

    def preflight(self) -> ProviderPreflight:
        executable = self.runner.which("codex") or ""
        installed = bool(executable)
        version = ""
        auth = ProviderAuthProfile(
            provider=self.name,
            auth_mode="existing_session",
            status="unavailable" if not installed else "unknown",
            detail="codex CLI not found" if not installed else "login status not checked",
        )
        notes: list[str] = []

        if installed:
            version_result = self.runner.run([executable, "--version"], timeout=10)
            version = (version_result.stdout or version_result.stderr).strip()
            login_result = self.runner.run([executable, "login", "status"], timeout=10)
            login_text = (login_result.stdout or login_result.stderr).strip()
            auth = ProviderAuthProfile(
                provider=self.name,
                auth_mode="existing_session",
                status="available" if login_result.returncode == 0 else "unknown",
                detail=login_text.splitlines()[0] if login_text else "no login status output",
            )
            if login_result.returncode != 0:
                notes.append("codex login status returned a non-zero exit code")

        return ProviderPreflight(
            provider=self.name,
            installed=installed,
            executable=executable,
            version=version,
            auth=auth,
            capabilities=self.capabilities(),
            notes=notes,
        )

    def start(self, run: FlowRun) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            summary=f"codex provider initialized run {run.run_id}",
            payload={"feature": run.feature, "mode": run.mode, "repos": list(run.repos)},
        )

    def headless_command(self, workspace_dir: Path, prompt: str) -> list[str]:
        executable = self.runner.which("codex") or "codex"
        return [
            executable,
            "exec",
            "--cd",
            str(workspace_dir),
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
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
