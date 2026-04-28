from __future__ import annotations

from .providers.claude import ClaudeFlowProvider
from .providers.codex import CodexFlowProvider
from .providers.fake import FakeFlowProvider
from .service import LocalFlowService
from .store import LocalFlowStore


def build_local_flow_service(cfg) -> LocalFlowService:  # noqa: ANN001
    return LocalFlowService(
        store=LocalFlowStore(cfg.worktree_root),
        providers={
            "fake": FakeFlowProvider(),
            "codex": CodexFlowProvider(),
            "claude": ClaudeFlowProvider(),
        },
        default_provider="fake",
    )
