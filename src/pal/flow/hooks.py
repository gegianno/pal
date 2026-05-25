from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from .models import FlowEvent, FlowRun
from .store import LocalFlowStore


@dataclass(frozen=True)
class FlowHook:
    name: str
    command: list[str]
    events: list[str] = field(default_factory=lambda: ["*"])

    def matches(self, event_type: str) -> bool:
        return "*" in self.events or event_type in self.events


@dataclass(frozen=True)
class HookCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class HookResult:
    hook: str
    event_id: str
    event_type: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class HookRunner(Protocol):
    def run(
        self,
        command: list[str],
        *,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> HookCommandResult: ...


class LocalHookRunner:
    def run(
        self,
        command: list[str],
        *,
        cwd: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> HookCommandResult:
        completed = subprocess.run(
            command,
            check=False,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return HookCommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class FlowHookDispatcher:
    def __init__(
        self,
        hooks: list[FlowHook] | None = None,
        *,
        runner: HookRunner | None = None,
        timeout: int = 30,
    ) -> None:
        self.hooks = list(hooks or [])
        self.runner = runner or LocalHookRunner()
        self.timeout = timeout

    def dispatch(
        self,
        *,
        event: FlowEvent,
        run: FlowRun,
        store: LocalFlowStore,
    ) -> list[HookResult]:
        results: list[HookResult] = []
        for hook in self.hooks:
            if not hook.matches(event.type):
                continue
            try:
                result = self.runner.run(
                    hook.command,
                    cwd=store.feature_dir(run.feature),
                    env=_hook_env(event, run),
                    timeout=self.timeout,
                )
            except Exception as exc:  # keep notification hooks best-effort.
                result = HookCommandResult(returncode=127, stderr=str(exc))
            hook_result = HookResult(
                hook=hook.name,
                event_id=event.id,
                event_type=event.type,
                command=hook.command,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            store.append_hook_result(run, hook_result.to_dict())
            results.append(hook_result)
        return results


def _hook_env(event: FlowEvent, run: FlowRun) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PAL_FLOW_EVENT_JSON": json.dumps(event.to_dict(), sort_keys=True),
            "PAL_FLOW_EVENT_PAYLOAD_JSON": json.dumps(event.payload, sort_keys=True),
            "PAL_FLOW_EVENT_ID": event.id,
            "PAL_FLOW_EVENT_TYPE": event.type,
            "PAL_FLOW_RUN_JSON": json.dumps(run.to_dict(), sort_keys=True),
            "PAL_FLOW_RUN_ID": run.run_id,
            "PAL_FLOW_FEATURE": run.feature,
            "PAL_FLOW_PHASE": event.phase.value if event.phase else "",
            "PAL_FLOW_STATUS": run.status.value,
            "PAL_FLOW_MODE": run.mode,
            "PAL_FLOW_WORKFLOW": run.workflow_name,
            "PAL_FLOW_WORK_TYPE": run.work_type,
            "PAL_FLOW_REPOS": "\n".join(run.repos),
            "PAL_FLOW_ARTIFACT_ROOT": run.artifact_root,
        }
    )
    return env
