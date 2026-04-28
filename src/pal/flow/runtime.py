from __future__ import annotations

from .providers.claude import ClaudeFlowProvider
from .providers.codex import CodexFlowProvider
from .providers.fake import FakeFlowProvider
from .hooks import FlowHook, FlowHookDispatcher
from .service import LocalFlowService
from .store import LocalFlowStore
from .workflows.library import LocalWorkflowLibrary
from ..workspaces import GitWorktreeWorkspaceBackend


def build_local_flow_service(cfg) -> LocalFlowService:  # noqa: ANN001
    return LocalFlowService(
        store=LocalFlowStore(cfg.worktree_root),
        providers={
            "fake": FakeFlowProvider(),
            "codex": CodexFlowProvider(codex=cfg.codex, agent_add_dirs=cfg.agent.add_dirs),
            "claude": ClaudeFlowProvider(claude=cfg.claude, agent_add_dirs=cfg.agent.add_dirs),
        },
        default_provider="fake",
        workflow_library=LocalWorkflowLibrary(cfg.root),
        workspace_backend=GitWorktreeWorkspaceBackend(cfg),
        hooks=FlowHookDispatcher(
            hooks=[
                FlowHook(name=hook.name, command=hook.command, events=hook.events)
                for hook in cfg.flow.hooks
            ]
        ),
    )
