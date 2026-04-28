# Changelog

All notable changes to this project will be documented in this file.

This project aims to follow Keep a Changelog and Semantic Versioning.

## Unreleased

- Breaking: replace `pal codex` / `pal exec` with `pal run`, `pal plan`, and `pal implement`.
- Add Claude Code CLI support via `pal run|plan|implement <feature> claude ...`.
- Add agent name completion for agent runner commands.
- `pal plan <feature> codex` now launches Codex with `/plan` as the initial prompt.
- Add `[claude]` config defaults: `permission_mode`, model, extra args, add_dirs, and bypass guardrail.
- Add `pal rename <old_feature> <new_feature>` to move linked worktrees safely.
- Set terminal title on agent launch to include feature and agent.
- Improve `pal rm` error handling for unregistered/orphan worktree paths (actionable guidance, no traceback).
- Add `--version` flag.
- Add shell autocompletion for common arguments.
- Add `[agent].add_dirs` support for extra writable roots.
- Make `pal rm` remove empty/broken feature directories.
- Add `pal flow` for checked-in agentic workflow specs, durable run state, phase briefs, headless
  phase execution, artifact gates, approvals, block/replan loops, run-loop policies, and hooks.
- Add `pal flow start --workspace state-only|create|reuse|validate` to keep flow state explicit
  while optionally preparing or validating git-worktree feature workspaces.
- Make Codex and Claude flow providers use local logged-in CLIs plus shared agent configuration for
  sandbox/permission modes, models, extra args, and writable roots.
- Add `pal flow init` with built-in `dev-complex` and `dev-routine` workflow templates for checked-in
  flow scaffolding.
- Add `pal flow ship` for durable shipping manifests plus explicit commit, push, and GitHub PR
  creation actions.
- Add `pal flow inspect`, `pal flow watch --follow`, richer hook environment payloads, and provider
  execution diagnostics in manifests.

## 0.2.0

- Introduce agent-first runner commands with intent wrappers:
  - `pal run <feature> <agent> [agent args...]`
  - `pal plan <feature> <agent> [agent args...]`
  - `pal implement <feature> <agent> [agent args...]`
- Add Claude Code support and map shared `[agent].add_dirs` to both Codex and Claude runners.
- Remove legacy `pal codex` and `pal exec` commands.

## 0.1.0

- Initial release.
