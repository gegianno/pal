from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..models import FlowRun


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

    def to_session_dict(self, *, session_id: str, started_at: str, ended_at: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "provider": self.provider,
            "execution_mode": self.execution_mode,
            "cwd": self.cwd,
            "launch_command": list(self.command),
            "started_at": started_at,
            "ended_at": ended_at,
            "status": self.status,
            "returncode": self.returncode,
        }


class FlowProvider(Protocol):
    name: str
    start: Callable[[FlowRun], ProviderResult]
    capabilities: Callable[[], ProviderCapabilities]
    preflight: Callable[[], ProviderPreflight]
    launch_headless: Callable[[ProviderLaunchRequest], ProviderLaunchResult]
