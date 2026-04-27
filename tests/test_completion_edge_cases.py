from __future__ import annotations

from pathlib import Path

import click
import pytest

from pal.completion import complete_agent, complete_feature, complete_repo, complete_repo_in_feature


def test_completion_returns_empty_when_feature_root_is_missing(tmp_path: Path) -> None:
    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path, "worktree_root": tmp_path / "missing", "branch_prefix": "bug"}

    assert complete_feature(ctx, [], "") == []


def test_completion_returns_empty_when_config_loading_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path}
    monkeypatch.setattr(
        "pal.completion.load_config", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError())
    )

    assert complete_feature(ctx, [], "") == []
    assert complete_repo(ctx, [], "") == []
    assert complete_repo_in_feature(ctx, [], "") == []


def test_complete_agent_returns_empty_when_agent_registry_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = click.Context(click.Command("pal"))
    monkeypatch.setattr("pal.completion.AGENTS", None)

    assert complete_agent(ctx, [], "") == []


def test_complete_repo_in_feature_returns_empty_without_feature_context(tmp_path: Path) -> None:
    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path}
    assert complete_repo_in_feature(ctx, [], "") == []

    ctx.params = {"root": tmp_path, "feature": "missing"}
    assert complete_repo_in_feature(ctx, [], "") == []
