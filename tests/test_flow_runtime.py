from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.runtime import build_local_flow_service
from pal.flow.store import LocalFlowStore
from pal.workspaces import GitWorktreeWorkspaceBackend


def test_build_local_flow_service_wires_store_and_fake_provider(tmp_path: Path) -> None:
    service = build_local_flow_service(
        SimpleNamespace(
            root=tmp_path,
            worktree_root=tmp_path / "_wt",
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
    assert service.hooks.hooks[0].name == "notify"
