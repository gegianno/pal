from __future__ import annotations

from ..models import FlowRun
from .base import (
    ProviderAuthProfile,
    ProviderCapabilities,
    ProviderLaunchRequest,
    ProviderLaunchResult,
    ProviderPreflight,
    ProviderResult,
)


class FakeFlowProvider:
    name = "fake"

    def start(self, run: FlowRun) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            summary=f"fake provider initialized run {run.run_id}",
            payload={"feature": run.feature, "mode": run.mode, "repos": list(run.repos)},
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            execution_modes=["local_headless"],
            resume=False,
            attach=False,
            json_output=True,
            stream_output=True,
            hooks=False,
            skills=False,
            native_subagents=False,
            mcp=False,
            sandbox_controls=False,
        )

    def preflight(self) -> ProviderPreflight:
        return ProviderPreflight(
            provider=self.name,
            installed=True,
            executable="fake",
            version="0",
            auth=ProviderAuthProfile(
                provider=self.name,
                auth_mode="none",
                status="available",
                detail="fake provider requires no credentials",
            ),
            capabilities=self.capabilities(),
        )

    def launch_headless(self, request: ProviderLaunchRequest) -> ProviderLaunchResult:
        return ProviderLaunchResult(
            provider=self.name,
            execution_mode="local_headless",
            command=["fake-flow-provider", request.prompt],
            cwd=str(request.workspace_dir),
            status="completed",
            returncode=0,
            stdout=f"fake provider completed run {request.run.run_id}\n",
            stderr="",
            diagnostics={
                "executable": "fake-flow-provider",
                "workspace_dir": str(request.workspace_dir),
                "output_dir": str(request.output_dir),
                "prompt_chars": len(request.prompt),
                "error": "",
            },
        )
