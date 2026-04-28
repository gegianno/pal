from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pal.flow.providers.fake import FakeFlowProvider
from pal.flow.runtime import build_local_flow_service
from pal.flow.store import LocalFlowStore


def test_build_local_flow_service_wires_store_and_fake_provider(tmp_path: Path) -> None:
    service = build_local_flow_service(SimpleNamespace(worktree_root=tmp_path / "_wt"))

    assert isinstance(service.store, LocalFlowStore)
    assert isinstance(service.provider("fake"), FakeFlowProvider)
    assert service.provider_names() == ["claude", "codex", "fake"]
    assert service.store.worktree_root == tmp_path / "_wt"
