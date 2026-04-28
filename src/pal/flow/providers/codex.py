from __future__ import annotations

import os
from pathlib import Path

from ...config import CodexConfig
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

    def __init__(
        self,
        runner: LocalCommandRunner | None = None,
        *,
        codex: CodexConfig | None = None,
        agent_add_dirs: list[str] | None = None,
    ) -> None:
        self.runner = runner or LocalCommandRunner()
        self.codex = codex or CodexConfig()
        self.agent_add_dirs = list(agent_add_dirs or [])

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
        command = [
            executable,
            "exec",
            "--cd",
            str(workspace_dir),
        ]
        if self.codex.full_auto:
            command.append("--full-auto")
        else:
            command += ["--sandbox", self.codex.sandbox]
        for raw_dir in self._effective_add_dirs():
            normalized = _normalize_add_dir(workspace_dir, raw_dir)
            if normalized:
                command += ["--add-dir", normalized]
        command += [
            "--skip-git-repo-check",
            "--json",
            prompt,
        ]
        return command

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        command = self.headless_command(request.workspace_dir, request.prompt)
        if not self.runner.which("codex"):
            return ProviderLaunchResult(
                provider=self.name,
                execution_mode="local_headless",
                command=command,
                cwd=str(request.workspace_dir),
                status="failed",
                returncode=127,
                stdout="",
                stderr="codex CLI not found on PATH. Install Codex or log in before execution.",
                diagnostics=_launch_diagnostics(command, request, error="missing_executable"),
            )
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
            diagnostics=_launch_diagnostics(command, request),
        )

    def _effective_add_dirs(self) -> list[str]:
        merged = [*self.agent_add_dirs, *self.codex.add_dirs]
        return list(dict.fromkeys(merged))


def _normalize_add_dir(workspace_dir: Path, raw: str) -> str:
    expanded = os.path.expandvars(str(raw).strip())
    if not expanded:
        return ""
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = workspace_dir / path
    return str(path.resolve())


def _launch_diagnostics(
    command: list[str],
    request: ProviderLaunchRequest,
    *,
    error: str = "",
) -> dict[str, object]:
    return {
        "executable": command[0] if command else "",
        "workspace_dir": str(request.workspace_dir),
        "output_dir": str(request.output_dir),
        "prompt_chars": len(request.prompt),
        "error": error,
    }
