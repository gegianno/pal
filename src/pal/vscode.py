from __future__ import annotations

import json
from pathlib import Path

from .git import is_git_repo


def write_code_workspace(feature_dir: Path, feature: str) -> Path:
    """
    Create/refresh <feature>.code-workspace inside the feature directory.
    Uses relative folder paths so the workspace can be moved.
    """
    folders = []
    for child in sorted(feature_dir.iterdir(), key=lambda p: p.name):
        if child.is_dir() and is_git_repo(child):
            folders.append({"path": child.name})

    ws_path = feature_dir / f"{feature}.code-workspace"
    ws_path.write_text(json.dumps({"folders": folders, "settings": {}}, indent=2) + "\n", encoding="utf-8")
    return ws_path
