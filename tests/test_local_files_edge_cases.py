from __future__ import annotations

from pathlib import Path

from pal.local_files import copy_local_files, resolve_local_file_paths


def test_resolve_local_file_paths_deduplicates_and_rejects_private_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("x\n", encoding="utf-8")
    (src / "dir").mkdir()
    (src / "file.txt").write_text("x\n", encoding="utf-8")

    resolved, invalid = resolve_local_file_paths(
        src,
        paths=["file.txt", "file.txt", ".git/config", "../bad"],
        patterns=["*", "/abs"],
    )

    assert ".git/config" not in resolved
    assert "file.txt" in resolved
    assert resolved.count("file.txt") == 1
    assert set(invalid) == {"../bad", "/abs"}


def test_copy_local_files_reports_missing_private_and_symlink_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("x\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (src / "link.txt").symlink_to(outside)

    result = copy_local_files(
        src,
        dst,
        paths=["missing.txt", ".git/config", "link.txt"],
        overwrite=False,
    )

    assert result.skipped_missing == [Path("missing.txt")]
    assert set(result.skipped_invalid) == {".git/config", "link.txt"}


def test_resolve_local_file_paths_rejects_globbed_symlinks_outside_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (src / "link.txt").symlink_to(outside)

    resolved, invalid = resolve_local_file_paths(src, patterns=["*"])

    assert "link.txt" not in resolved
    assert invalid == []


def test_resolve_local_file_paths_ignores_glob_matches_outside_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()

    class OutsideMatch:
        def relative_to(self, _base):  # noqa: ANN001
            raise ValueError("outside")

    class FakeSource:
        def resolve(self) -> Path:
            return src.resolve()

        def glob(self, _pattern: str):  # noqa: ANN201
            return [OutsideMatch()]

    resolved, invalid = resolve_local_file_paths(FakeSource(), patterns=["*"])  # type: ignore[arg-type]

    assert resolved == []
    assert invalid == []
