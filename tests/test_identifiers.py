from __future__ import annotations

from pathlib import Path

import pytest

from pal.identifiers import (
    IdentifierError,
    normalize_feature_name,
    normalize_identifier,
    normalize_repo_name,
    normalize_run_id,
    safe_child_path,
)


def test_normalize_identifier_accepts_safe_tokens_and_strips_whitespace() -> None:
    assert normalize_feature_name(" feature_1 ") == "feature_1"
    assert normalize_repo_name("api.repo-1") == "api.repo-1"
    assert normalize_run_id("run_" + "a" * 116) == "run_" + "a" * 116


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (" ", "must not be empty"),
        ("/tmp/evil", "single safe path segment"),
        ("../evil", "single safe path segment"),
        (".", "single safe path segment"),
        ("..", "single safe path segment"),
        ("a" * 81, "80 characters or fewer"),
        ("bad name", "letters, numbers"),
    ],
)
def test_normalize_identifier_rejects_unsafe_tokens(value: str, message: str) -> None:
    with pytest.raises(IdentifierError, match=message):
        normalize_identifier(value, "Thing")


def test_normalize_run_id_uses_run_id_length_limit() -> None:
    with pytest.raises(IdentifierError, match="120 characters or fewer"):
        normalize_run_id("r" * 121)


def test_safe_child_path_keeps_children_under_root(tmp_path: Path) -> None:
    assert safe_child_path(tmp_path, "api", "Repo") == tmp_path.resolve() / "api"

    with pytest.raises(IdentifierError, match="must stay under"):
        safe_child_path(tmp_path, "../outside", "Repo")
