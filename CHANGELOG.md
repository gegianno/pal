# Changelog

All notable changes to this project will be documented in this file.

This project aims to follow Keep a Changelog and Semantic Versioning.

## Unreleased

- Breaking: replace `pal codex` / `pal exec` with `pal run`, `pal plan`, and `pal implement`.
- Add Claude Code CLI support via `pal run|plan|implement <feature> claude ...`.
- Add agent name completion for agent runner commands.
- `pal plan <feature> codex` now launches Codex with `/plan` as the initial prompt.
- Add `[claude]` config defaults: `permission_mode`, model, extra args, add_dirs, and bypass guardrail.
- Add `--version` flag.
- Add shell autocompletion for common arguments.
- Add `[agent].add_dirs` support for extra writable roots.
- Make `pal rm` remove empty/broken feature directories.

## 0.2.0

- Introduce agent-first runner commands with intent wrappers:
  - `pal run <feature> <agent> [agent args...]`
  - `pal plan <feature> <agent> [agent args...]`
  - `pal implement <feature> <agent> [agent args...]`
- Add Claude Code support and map shared `[agent].add_dirs` to both Codex and Claude runners.
- Remove legacy `pal codex` and `pal exec` commands.

## 0.1.0

- Initial release.
