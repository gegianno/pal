from __future__ import annotations

from .providers.fake import FakeFlowProvider
from .service import LocalFlowService
from .store import LocalFlowStore


def build_local_flow_service(cfg) -> LocalFlowService:  # noqa: ANN001
    return LocalFlowService(
        store=LocalFlowStore(cfg.worktree_root),
        provider=FakeFlowProvider(),
    )
