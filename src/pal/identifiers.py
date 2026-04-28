from __future__ import annotations

import re
from pathlib import Path

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DEFAULT_MAX_LENGTH = 80


class IdentifierError(ValueError):
    pass


def normalize_identifier(
    value: str,
    label: str,
    *,
    max_length: int = _DEFAULT_MAX_LENGTH,
) -> str:
    identifier = str(value).strip()
    if not identifier:
        raise IdentifierError(f"{label} must not be empty.")
    path = Path(identifier)
    if path.is_absolute():
        raise IdentifierError(f"{label} must be a single safe path segment: {value}")
    if len(path.parts) != 1:
        raise IdentifierError(f"{label} must be a single safe path segment: {value}")
    if identifier in {".", ".."}:
        raise IdentifierError(f"{label} must be a single safe path segment: {value}")
    if len(identifier) > max_length:
        raise IdentifierError(f"{label} must be {max_length} characters or fewer: {value}")
    if not _SAFE_IDENTIFIER_RE.fullmatch(identifier):
        raise IdentifierError(
            f"{label} must start with a letter or number and contain only "
            f"letters, numbers, '.', '_' or '-': {value}"
        )
    return identifier


def normalize_feature_name(value: str) -> str:
    return normalize_identifier(value, "Feature name")


def normalize_repo_name(value: str) -> str:
    return normalize_identifier(value, "Repo name")


def normalize_run_id(value: str) -> str:
    return normalize_identifier(value, "Run ID", max_length=120)


def safe_child_path(root: Path, child: str, label: str) -> Path:
    root_path = root.expanduser().resolve()
    child_path = (root_path / child).resolve()
    try:
        child_path.relative_to(root_path)
    except ValueError as exc:
        raise IdentifierError(f"{label} must stay under {root_path}: {child}") from exc
    return child_path
