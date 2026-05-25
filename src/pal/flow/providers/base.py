from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..models import FlowRun


PROVIDER_STATE_ERROR = "provider_state_inaccessible"


def provider_state_access_error(provider: str, stderr: str) -> str:
    text = stderr.lower()
    provider_name = provider.lower()
    if provider_name == "codex" and _codex_state_access_blocked(text):
        return _provider_state_message(
            provider="Codex",
            state_dir="CODEX_HOME or ~/.codex",
            stderr=stderr,
        )
    if provider_name == "claude" and _claude_state_access_blocked(text):
        return _provider_state_message(
            provider="Claude Code",
            state_dir="Claude Code's logged-in state directory",
            stderr=stderr,
        )
    return ""


def _codex_state_access_blocked(text: str) -> bool:
    return (
        "codex cannot access session files" in text
        or ("attempt to write a readonly database" in text and ".codex" in text)
        or ("failed to persist config.toml" in text and ".codex" in text)
    )


def _claude_state_access_blocked(text: str) -> bool:
    blocked = (
        "permission denied" in text
        or "operation not permitted" in text
        or "eacces" in text
        or "readonly" in text
    )
    return blocked and (".claude" in text or "claude" in text and "state" in text)


def _provider_state_message(*, provider: str, state_dir: str, stderr: str) -> str:
    original = stderr.strip()
    return (
        f"{provider} provider state is not accessible.\n\n"
        f"pal is using the local {provider} CLI with an existing logged-in account. "
        f"Run pal from a process that can access {state_dir}, or grant that provider state "
        "directory to the outer sandbox. Do not copy provider credentials into the worktree, "
        "and do not pass provider state directories as agent writable roots.\n\n"
        f"Original provider stderr:\n{original}"
    ).strip()


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    execution_modes: list[str]
    resume: bool
    attach: bool
    json_output: bool
    stream_output: bool
    hooks: bool
    skills: bool
    native_subagents: bool
    mcp: bool
    sandbox_controls: bool
    fallbacks: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "execution_modes": list(self.execution_modes),
            "resume": self.resume,
            "attach": self.attach,
            "json_output": self.json_output,
            "stream_output": self.stream_output,
            "hooks": self.hooks,
            "skills": self.skills,
            "native_subagents": self.native_subagents,
            "mcp": self.mcp,
            "sandbox_controls": self.sandbox_controls,
            "fallbacks": dict(self.fallbacks),
        }


@dataclass(frozen=True)
class ProviderAuthProfile:
    provider: str
    auth_mode: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "auth_mode": self.auth_mode,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ProviderPreflight:
    provider: str
    installed: bool
    executable: str
    version: str
    auth: ProviderAuthProfile
    capabilities: ProviderCapabilities
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "installed": self.installed,
            "executable": self.executable,
            "version": self.version,
            "auth": self.auth.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class ProviderLaunchRequest:
    run: FlowRun
    workspace_dir: Path
    prompt: str
    output_dir: Path
    writable_dirs: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderLaunchResult:
    provider: str
    execution_mode: str
    command: list[str]
    cwd: str
    status: str
    returncode: int
    stdout: str
    stderr: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_session_dict(
        self,
        *,
        session_id: str,
        started_at: str,
        ended_at: str,
        command: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "provider": self.provider,
            "execution_mode": self.execution_mode,
            "cwd": self.cwd,
            "launch_command": list(command if command is not None else self.command),
            "started_at": started_at,
            "ended_at": ended_at,
            "status": self.status,
            "returncode": self.returncode,
            "diagnostics": dict(self.diagnostics),
        }


class FlowProvider(Protocol):
    name: str
    start: Callable[[FlowRun], ProviderResult]
    capabilities: Callable[[], ProviderCapabilities]
    preflight: Callable[[], ProviderPreflight]
    launch_headless: Callable[[ProviderLaunchRequest], ProviderLaunchResult]
