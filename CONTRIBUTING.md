# Contributing to pal

Thanks for taking the time to contribute!

## Getting started

### Prerequisites

- Python 3.9+
- Git

### Setup (recommended)

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -e ".[test,dev]"
```

Run tests:

```bash
./.venv/bin/python -m pytest
```

Run lint/format (Ruff):

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m ruff format .
```

Optional (but recommended): install git hooks

```bash
./.venv/bin/pre-commit install
```

## Project guidelines

- Keep changes focused and small when possible.
- Prefer fixing root causes over workarounds.
- Add tests for new behavior and bug fixes.
- Keep CLI UX stable; avoid breaking flags without a clear migration path.

## Reporting bugs / requesting features

Please use GitHub Issues. Include:

- your OS + Python version
- the `pal` command you ran and expected behavior
- actual output (copy/paste)
- a minimal folder layout (example repos + `_wt/` structure)

## Security issues

Please follow `SECURITY.md` for responsible disclosure.
