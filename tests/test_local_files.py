from __future__ import annotations

from pathlib import Path

from pal.local_files import copy_local_files, resolve_local_file_paths


def test_copy_local_files_copies_and_respects_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / ".env").write_text("A=1\n", encoding="utf-8")
    (src / "nested").mkdir()
    (src / "nested" / ".npmrc").write_text("//registry/:_authToken=abc\n", encoding="utf-8")

    result = copy_local_files(src, dst, paths=[".env", "nested/.npmrc"], overwrite=False)
    assert set(p.as_posix() for p in result.copied) == {".env", "nested/.npmrc"}
    assert (dst / ".env").read_text(encoding="utf-8") == "A=1\n"

    # No overwrite by default
    (src / ".env").write_text("A=2\n", encoding="utf-8")
    result2 = copy_local_files(src, dst, paths=[".env"], overwrite=False)
    assert result2.copied == []
    assert (dst / ".env").read_text(encoding="utf-8") == "A=1\n"

    # Overwrite when enabled
    result3 = copy_local_files(src, dst, paths=[".env"], overwrite=True)
    assert result3.copied == [Path(".env")]
    assert (dst / ".env").read_text(encoding="utf-8") == "A=2\n"


def test_copy_local_files_rejects_path_traversal(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / ".env").write_text("A=1\n", encoding="utf-8")

    result = copy_local_files(src, dst, paths=["../.env", "/abs/path"], overwrite=False)
    assert result.copied == []
    assert set(result.skipped_invalid) == {"../.env", "/abs/path"}


def test_resolve_local_file_paths_globs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".env").write_text("A=1\n", encoding="utf-8")
    (src / ".env.local").write_text("A=2\n", encoding="utf-8")
    (src / "nested").mkdir()
    (src / "nested" / ".env.prod").write_text("A=3\n", encoding="utf-8")
    (src / "nested" / ".npmrc").write_text("x\n", encoding="utf-8")
    (src / "node_modules").mkdir()
    (src / "node_modules" / ".env").write_text("SHOULD_NOT_MATCH\n", encoding="utf-8")

    resolved, invalid = resolve_local_file_paths(
        src,
        patterns=["**/.env*", "**/.npmrc"],
    )
    assert invalid == []
    assert set(resolved) == {".env", ".env.local", "nested/.env.prod", "nested/.npmrc"}
