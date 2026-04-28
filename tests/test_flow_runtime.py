from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pal.config import AgentConfig, ClaudeConfig, CodexConfig
from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.runtime import build_local_flow_service
from pal.flow.store import LocalFlowStore
from pal.workspaces import GitWorktreeWorkspaceBackend


def test_build_local_flow_service_wires_store_and_fake_provider(tmp_path: Path) -> None:
    service = build_local_flow_service(
        SimpleNamespace(
            root=tmp_path,
            worktree_root=tmp_path / "_wt",
            agent=AgentConfig(add_dirs=["/tmp/shared"]),
            codex=CodexConfig(add_dirs=["/tmp/codex"]),
            claude=ClaudeConfig(add_dirs=["/tmp/claude"]),
            flow=SimpleNamespace(
                hooks=[
                    SimpleNamespace(name="notify", command=["echo", "ok"], events=["*"]),
                ]
            ),
        )
    )

    assert isinstance(service.store, LocalFlowStore)
    assert isinstance(service.provider("fake"), FakeFlowProvider)
    assert service.provider_names() == ["claude", "codex", "fake"]
    assert service.store.worktree_root == tmp_path / "_wt"
    assert service.workflow_paths() == []
    assert isinstance(service.workspace_backend, GitWorktreeWorkspaceBackend)
    assert service.provider("codex").agent_add_dirs == ["/tmp/shared"]
    assert service.provider("claude").agent_add_dirs == ["/tmp/shared"]
    assert service.hooks.hooks[0].name == "notify"
