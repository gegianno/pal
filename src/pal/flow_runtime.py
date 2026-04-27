from __future__ import annotations

from .flow_provider import FakeFlowProvider
from .flow_service import LocalFlowService
from .flow_store import LocalFlowStore


def build_local_flow_service(cfg) -> LocalFlowService:  # noqa: ANN001
    return LocalFlowService(
        store=LocalFlowStore(cfg.worktree_root),
        provider=FakeFlowProvider(),
    )
