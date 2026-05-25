from __future__ import annotations

import os
from pathlib import Path

from ...config import ClaudeConfig
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


class ClaudeFlowProvider:
    name = "claude"

    def __init__(
        self,
        runner: LocalCommandRunner | None = None,
        *,
        claude: ClaudeConfig | None = None,
        agent_add_dirs: list[str] | None = None,
    ) -> None:
        self.runner = runner or LocalCommandRunner()
        self.claude = claude or ClaudeConfig()
        self.agent_add_dirs = list(agent_add_dirs or [])

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

    def headless_command(
        self,
        workspace_dir: Path,
        _prompt: str,
        writable_dirs: list[Path] | None = None,
    ) -> list[str]:
        executable = self.runner.which("claude") or "claude"
        args = list(self.claude.extra_args)
        if self.claude.model and not _has_flag(args, "--model"):
            args += ["--model", self.claude.model]
        if not _has_flag(args, "--permission-mode"):
            mode = self.claude.permission_mode.strip()
            if mode:
                args += ["--permission-mode", mode]
        if not _has_flag(args, "--output-format"):
            args += ["--output-format", "stream-json"]
        if not _has_flag(args, "--input-format"):
            args += ["--input-format", "text"]
        _validate_permissions(self.claude, args)

        command = [executable]
        for raw_dir in [*self._effective_add_dirs(), *(str(path) for path in writable_dirs or [])]:
            normalized = _normalize_add_dir(workspace_dir, raw_dir)
            if normalized:
                command += ["--add-dir", normalized]
        command += [
            "-p",
            *args,
        ]
        return command

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        command = self.headless_command(
            request.workspace_dir,
            request.prompt,
            request.writable_dirs,
        )
        if not self.runner.which("claude"):
            return ProviderLaunchResult(
                provider=self.name,
                execution_mode="local_headless",
                command=command,
                cwd=str(request.workspace_dir),
                status="failed",
                returncode=127,
                stdout="",
                stderr=(
                    "claude CLI not found on PATH. Install Claude Code or log in before execution."
                ),
                diagnostics=_launch_diagnostics(command, request, error="missing_executable"),
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
            diagnostics=_launch_diagnostics(command, request, error=error),
        )

    def _effective_add_dirs(self) -> list[str]:
        merged = [*self.agent_add_dirs, *self.claude.add_dirs]
        return list(dict.fromkeys(merged))


def _normalize_add_dir(workspace_dir: Path, raw: str) -> str:
    expanded = os.path.expandvars(str(raw).strip())
    if not expanded:
        return ""
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = workspace_dir / path
    return str(path.resolve())


def _has_flag(args: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in args)


def _flag_value(args: list[str], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == name:
            return args[index + 1] if index + 1 < len(args) else None
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return None


def _is_bypass_permission_mode(mode: str) -> bool:
    normalized = mode.strip().lower()
    return normalized in {"bypasspermissions", "bypass_permissions", "bypass-permissions"}


def _validate_permissions(claude: ClaudeConfig, args: list[str]) -> None:
    if claude.allow_bypass_permissions:
        return
    if _has_flag(args, "--dangerously-skip-permissions"):
        raise ValueError(
            "Bypass permissions are disabled by config ([claude].allow_bypass_permissions=false)."
        )
    mode = _flag_value(args, "--permission-mode")
    if mode and _is_bypass_permission_mode(mode):
        raise ValueError(
            "Bypass permissions mode is disabled by config "
            "([claude].allow_bypass_permissions=false)."
        )


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
        "prompt_transport": "stdin",
        "writable_dirs": [str(path) for path in request.writable_dirs],
        "error": error,
    }
