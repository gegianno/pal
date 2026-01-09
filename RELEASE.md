# Release process

This repository uses a simple manual release process.

1. Update version in `pyproject.toml` (`[project].version`).
2. Update `CHANGELOG.md`:
   - Move items from “Unreleased” into a new version section.
3. Run checks locally:
   - `python -m pytest`
   - `python -m ruff check .`
   - `python -m ruff format .`
4. Tag the release:
   - `git tag vX.Y.Z`
   - `git push --tags`
5. Publish to PyPI (if/when configured).
