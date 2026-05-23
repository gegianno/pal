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
    PROVIDER_STATE_ERROR,
    provider_state_access_error,
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
            state_error = provider_state_access_error(self.name, login_text)
            auth = ProviderAuthProfile(
                provider=self.name,
                auth_mode="existing_session",
                status=(
                    "available"
                    if login_result.returncode == 0
                    else "unavailable"
                    if state_error
                    else "unknown"
                ),
                detail=(
                    state_error.splitlines()[0]
                    if state_error
                    else login_text.splitlines()[0]
                    if login_text
                    else "no login status output"
                ),
            )
            if login_result.returncode != 0:
                notes.append(
                    "codex provider state is inaccessible"
                    if state_error
                    else "codex login status returned a non-zero exit code"
                )

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

    def headless_command(self, workspace_dir: Path, _prompt: str) -> list[str]:
        executable = self.runner.which("codex") or "codex"
        command = [executable]
        headless_approval = self.codex.headless_approval.strip()
        if headless_approval and not self.codex.full_auto:
            command += ["--ask-for-approval", headless_approval]
        command += ["exec", "--cd", str(workspace_dir)]
        if self.codex.headless_ephemeral:
            command.append("--ephemeral")
        if self.codex.headless_ignore_user_config:
            command.append("--ignore-user-config")
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
            "-",
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
                diagnostics=_launch_diagnostics(
                    command,
                    request,
                    headless_ephemeral=self.codex.headless_ephemeral,
                    error="missing_executable",
                ),
            )
        result = self.runner.run(command, cwd=request.workspace_dir, input_text=request.prompt)
        state_error = provider_state_access_error(self.name, result.stderr)
        stderr = state_error or result.stderr
        error = PROVIDER_STATE_ERROR if state_error else ""
        return ProviderLaunchResult(
            provider=self.name,
            execution_mode="local_headless",
            command=command,
            cwd=str(request.workspace_dir),
            status="completed" if result.returncode == 0 else "failed",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=stderr,
            diagnostics=_launch_diagnostics(
                command,
                request,
                headless_ephemeral=self.codex.headless_ephemeral,
                error=error,
            ),
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
    headless_ephemeral: bool,
    error: str = "",
) -> dict[str, object]:
    return {
        "executable": command[0] if command else "",
        "workspace_dir": str(request.workspace_dir),
        "output_dir": str(request.output_dir),
        "prompt_chars": len(request.prompt),
        "prompt_transport": "stdin",
        "headless_ephemeral": headless_ephemeral,
        "error": error,
    }
