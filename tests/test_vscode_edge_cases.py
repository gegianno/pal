from __future__ import annotations

from pathlib import Path

import pytest

from pal.vscode import write_code_workspace


def test_write_code_workspace_handles_feature_without_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "not-repo").mkdir()
    monkeypatch.setattr("pal.vscode.is_git_repo", lambda _p: False)

    ws = write_code_workspace(tmp_path, "feat")

    assert '"folders": []' in ws.read_text(encoding="utf-8")
