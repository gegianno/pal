from __future__ import annotations

from pathlib import Path

import click

from pal.completion import complete_feature, complete_repo, complete_repo_in_feature


def test_complete_feature_lists_feature_dirs(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "_wt" / "email").mkdir(parents=True)
    (tmp_path / "_wt" / "property-assign").mkdir(parents=True)

    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path}

    out = complete_feature(ctx, [], "pro")
    assert out == ["property-assign"]


def test_complete_repo_lists_repos(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    # Don't require git in tests; stub repo discovery.
    monkeypatch.setattr("pal.completion.list_child_repos", lambda _root: ["repo1", "repo2"])

    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path}

    out = complete_repo(ctx, [], "repo")
    assert out == ["repo1", "repo2"]


def test_complete_repo_in_feature_lists_only_git_repos(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    feature_dir = tmp_path / "_wt" / "feat-x"
    (feature_dir / "repo1" / ".git").mkdir(parents=True)
    (feature_dir / "not-a-repo").mkdir(parents=True)

    monkeypatch.setattr("pal.completion.is_git_repo", lambda p: (p / ".git").exists())

    ctx = click.Context(click.Command("pal"))
    ctx.params = {"root": tmp_path, "feature": "feat-x"}

    out = complete_repo_in_feature(ctx, [], "")
    assert out == ["repo1"]
