# pal — Feature workspaces for multi-repo git worktrees

`pal` helps you create **feature-scoped “sandboxes”** across multiple repos using **Git worktrees**.
It’s designed for running **multiple coding-agent sessions locally** without stepping on each other.

It also generates a **VS Code / Cursor multi-root workspace** per feature so you can review changes easily.

---

## Why this exists

- Git worktrees are **per repository**, but features often span multiple repos.
- Coding agents (Codex, etc.) work best when their writable surface area is **small**.
- You want **projects-level visibility**, but **feature-level isolation**.

`pal` gives you a simple model:

```
<root>/
  repo1/ repo2/ repo3/ ...             # your normal checkouts (“human only”)
  _wt/
    feat-auth/
      repo1/ repo3/ ...                # worktrees used by agents for that feature
      feat-auth.code-workspace         # multi-root workspace for review
```

---

## Install

### Option A: pipx (recommended)

```bash
pipx install .
```

### Option B: uv

```bash
uv tool install .
```

---

## Quickstart

From your projects directory (the folder that contains `repo1/ repo2/ ...`):

```bash
pal doctor
pal repos

pal new feat-auth repo1 repo3
pal open feat-auth       # opens a multi-root workspace in Cursor/VS Code (if detected)
pal rename feat-auth feat-auth-v2

pal run feat-auth codex                # interactive Codex
pal run feat-auth claude               # interactive Claude Code
pal plan feat-auth codex "Plan this feature"
pal implement feat-auth claude "Implement X and run tests"

# You can also forward agent CLI subcommands/options directly:
pal run feat-auth codex resume 019b947b-ff0f-7ff3-8a49-4723ee751f20
```

Intent behavior:

- `pal run`: forwards agent CLI args, then applies agent defaults from config.
- `pal plan`:
  - `claude`: injects `--permission-mode plan` unless already set.
  - `codex`: sends `/plan` as the initial prompt (accepts prompt text, not raw Codex flags).
- `pal implement`:
  - `claude`: injects `[claude].permission_mode` unless already set.
  - `codex`: same runtime behavior as `run` (Codex has no dedicated plan permission mode flag).

`pal run`/`plan`/`implement` forward extra args directly to the agent CLI, so no `--` separator is required.

If later you realize you need another repo:

```bash
pal add feat-auth repo4
```

Cleanup:

```bash
pal rm feat-auth         # removes all worktrees under _wt/feat-auth
# or remove a subset:
pal rm feat-auth --repo repo1 --repo repo3
```

Terminal tabs:

- When launching an agent, `pal` sets the terminal title to `pal <feature> (<agent>)`
  when the terminal supports OSC titles.

---

## Agentic flow specs

`pal flow` is the local-first workflow layer for checked-in agentic development flows. Specs live
in `.pal/flows/*.yaml` inside the project root, so teams can iterate on phases, policies, agents,
providers, artifacts, and transitions without changing `pal` code.

Create a starter spec:

```bash
pal flow init --list-templates
pal flow init dev-complex --template dev-complex --provider codex --repo api --repo web
pal flow init dev-routine --template dev-routine --provider claude
pal flow inspect dev-complex
```

Example `.pal/flows/dev-complex.yaml`:

```yaml
version: 1
name: dev-complex
work_type: dev
mode: complex
repos:
  - api
defaults:
  provider: codex
phases:
  - id: explore
    policy: co-driver
    required_artifacts:
      - artifacts/explore.md
    transitions:
      - on: complete
        to: design
      - on: blocked
        to: design
    agents:
      - id: codebase-explorer
        role: codebase exploration
        provider: codex
        prompt: Inspect the relevant repos and identify risks.
        produces:
          - artifacts/explore.md
        requires:
          - local_headless
          - json_output
  - id: design
    policy: supervisor
    requires_approval: true
    agents: []
```

Validate specs and start a workflow-backed run:

```bash
pal flow providers
pal flow init dev-complex --template dev-complex --provider codex --repo api
pal flow validate dev-complex
pal flow inspect dev-complex
pal flow start feat-auth --workflow dev-complex
pal flow start feat-auth --workflow dev-complex --workspace reuse
pal flow render feat-auth
pal flow execute feat-auth
pal flow artifacts feat-auth
pal flow run feat-auth
pal flow approve feat-auth
pal flow advance feat-auth
pal flow block feat-auth --reason "implementation hit a dependency issue"
pal flow replan feat-auth
pal flow ship feat-auth --dry-run
pal flow status feat-auth
pal flow watch feat-auth --follow
```

Provider execution is still explicit and safe by default: `pal flow start` records durable run state
and provider preflight metadata, but only runs a local headless agent when `--headless --prompt ...`
is passed.

Workspace preparation is explicit in V1. By default, `pal flow start` uses `--workspace state-only`
and only records flow state. Use `--workspace reuse` to create any missing repo worktrees while
reusing existing ones, `--workspace create` to fail if a requested repo worktree already exists, or
`--workspace validate` to require that the requested feature workspace and repo worktrees already
exist. Prepared workspace metadata is stored in `.pal/runs/<run-id>/workspace.json`.

Phase progression is explicit too. `advance` follows workflow transitions, `approve` satisfies
`requires_approval: true` gates, and `block`/`replan` records replanning loops without hiding them
inside agent output.

Workflow specs are intentionally template-like YAML:

- `version`, `name`, `work_type`, `mode`, and `repos` describe the workflow identity and default repo
  set.
- `defaults.provider` selects the local provider adapter (`codex`, `claude`, or `fake` in tests).
- Each `phase` has an `id`, `policy`, optional `requires_approval`, `required_artifacts`, and
  `transitions`.
- Each phase `agent` has an `id`, `role`, optional provider override, prompt, produced artifacts,
  and capability requirements such as `local_headless` or `json_output`.

Use `pal flow inspect <workflow>` after edits to review the resolved phase/agent structure, then
`pal flow validate <workflow>` to catch unsupported providers or capability requirements.

`pal flow render <feature>` compiles the current phase into durable
`.pal/runs/<run-id>/phase/<phase>/brief.md` and `brief.json` artifacts. The brief includes run
state, the active policy, resolved phase agents, required artifacts, recent events, approvals,
blockers, and small provider-specific guidance for the selected provider.

`pal flow execute <feature>` renders the current phase, runs each rendered phase agent through the
selected provider's local headless adapter, and records per-agent prompts, stdout/stderr logs, and
manifests under `.pal/runs/<run-id>/phase/<phase>/executions/<execution-id>/`. Execution records are
durable, but V1 execution does not auto-advance the workflow; `approve`, `advance`, `block`, and
`replan` remain explicit state changes.

Flow provider execution uses the local Codex and Claude Code CLIs, so it can use your already
logged-in accounts instead of API keys. Codex flow execution applies `[codex].sandbox`,
`[codex].full_auto`, `[agent].add_dirs`, and `[codex].add_dirs` to `codex exec`. Claude flow
execution applies `[claude].permission_mode`, `[claude].model`, `[claude].extra_args`,
`[agent].add_dirs`, and `[claude].add_dirs` to `claude -p`. The Claude bypass-permission guardrail
also applies to flow execution. Execution manifests include the exact command, working directory,
provider status, return code, stdout/stderr paths, and provider diagnostics such as resolved
executable, output directory, and prompt size.

`pal flow artifacts <feature>` validates the current phase's `required_artifacts`. Relative artifact
paths resolve under `.pal/artifacts`; an `artifacts/...` prefix is accepted and normalized there too.
`pal flow advance` refuses to complete a phase with missing required artifacts unless
`--force-artifacts` is passed.

`pal flow ship <feature>` prepares review/PR handoff artifacts under `.pal/runs/<run-id>/ship/`.
By default it is a safe dry run that records repo status, branch, diff stats, and the action plan.
When explicitly requested, it can commit (`--commit --message ...`), push (`--push`), and create or
reuse GitHub PRs through the local `gh` CLI (`--create-pr --no-dry-run`). Failures are recorded in
the durable ship manifest instead of being hidden in terminal output.

`pal flow run <feature>` is the policy-aware phase loop:

- `observer`: renders the phase and stops before execution.
- `supervisor`: executes and validates artifacts, then waits for explicit human advance.
- `co-driver`: behaves like `supervisor` unless `--co-driver-auto-advance` is passed.
- `autonomous`: executes, validates artifacts, and advances until completion, a gate, a failure, a
  missing artifact, or `--max-phases`.

Local hooks can be configured in `.pal.toml` and run after matching flow events. Hook results are
recorded in `.pal/runs/<run-id>/hooks.jsonl`; hook failures do not fail the flow command. Hook
commands receive event JSON plus run context in environment variables such as `PAL_FLOW_EVENT_JSON`,
`PAL_FLOW_RUN_JSON`, `PAL_FLOW_WORKFLOW`, `PAL_FLOW_WORK_TYPE`, `PAL_FLOW_REPOS`, and
`PAL_FLOW_ARTIFACT_ROOT`.

```toml
[[flow.hooks]]
name = "notify-failure"
command = ["osascript", "-e", "display notification \"pal flow failed\""]
events = ["flow.phase.execution.failed"]
```

---

## Agent defaults (important)

When you run Codex via `pal run ... codex` (or `pal plan` / `pal implement`), `pal` launches it with:

- `--cd <feature_workspace_dir>` to set the workspace root
- `--sandbox workspace-write` to restrict writes to that workspace directory

These are **per-invocation flags**, so your global `~/.codex/config.toml` defaults remain unchanged.
For Claude Code, `pal` launches inside the feature workspace directory and forwards shared writable roots
from `[agent].add_dirs` and `[claude].add_dirs` as repeated `--add-dir` flags.
Claude defaults can be configured via `[claude]` (`permission_mode`, model, extra args, bypass guardrail).

---

## Configuration (no hard-coded folder names)

`pal` is intentionally not tied to a specific directory layout.

### Config file discovery

`pal` loads config in this precedence order:

1. CLI flags
2. Local config: `<root>/.pal.toml`
3. Global config (XDG): `~/.config/pal/config.toml` (or platform equivalent)

To create a local config:

```bash
pal config init
```

### Example `.pal.toml`

```toml
# Root folder that contains your repos (defaults to current directory)
root = "."

# Where feature workspaces live (defaults to "<root>/_wt")
worktree_root = "_wt"

# How branches are named inside each repo worktree
branch_prefix = "feat"

# Repos discovery (optional):
# If you set this, pal will only consider these as repos.
repos = ["repo1", "repo2", "repo3", "repo4", "repo5"]

# Prefer "cursor" or "code" (auto-detected if omitted)
editor = "cursor"

[codex]
sandbox = "workspace-write"   # read-only | workspace-write | danger-full-access
approval = "on-request"       # untrusted | on-failure | on-request | never
full_auto = false             # if true, passes --full-auto

[claude]
permission_mode = "acceptEdits"            # default for `pal run` and `pal implement` with claude
# `pal plan ... claude` internally defaults to --permission-mode plan.
model = "sonnet"                           # optional; omit to use Claude CLI default
add_dirs = ["~/.claude"]                   # optional claude-specific writable roots
extra_args = []                            # optional args prepended to every claude invocation
allow_bypass_permissions = false           # blocks bypass permission flags/modes when false

[agent]
# Optional extra writable roots that apply to all agent runners (Codex, Claude Code).
# Useful when tools need to write caches under your home directory (e.g. ~/.npm, ~/.cache/prisma).
# pal forwards these as repeated `--add-dir` flags.
add_dirs = ["~/.npm", "~/.cache/prisma"]

[local_files]
# If enabled, pal can copy local (uncommitted/ignored) files (like `.env`, `.npmrc`) from your
# “human” checkouts into new feature worktrees to speed up setup.
enabled = false
overwrite = false
# paths = ["backend/.env.prod.local"]
# patterns = ["**/.env*", "**/.npmrc", "**/.envrc"]

# [local_files.repos.integrations]
# paths = ["apps/searcher/collections/.env"]
# patterns = ["apps/**/.npmrc"]
```

---

## UX tips

### Shell completions

Typer supports built-in completion installers:

```bash
pal --install-completion
# or:
pal --show-completion
```

In zsh, after installing, restart your shell (or `exec zsh`) so `pal` completions load.

### Keep a “global overview” editor window

You can still open your big `projects/` folder in Cursor for global visibility, while also opening
feature workspaces (`pal open <feature>`) for focused review.

---

## Commands

```bash
pal --help

pal repos
pal new <feature> <repo...>
pal add <feature> <repo...>
pal ls
pal status <feature>
pal open <feature>
pal rename <old_feature> <new_feature>
pal run <feature> <agent> [agent args...]
pal plan <feature> <agent> [agent args...]
pal implement <feature> <agent> [agent args...]
pal flow init [workflow] [--template dev-complex|dev-routine]
pal flow providers
pal flow validate [workflow]
pal flow inspect <workflow>
pal flow start <feature> [--workflow workflow] [--workspace state-only|create|reuse|validate]
pal flow render <feature>
pal flow execute <feature>
pal flow artifacts <feature>
pal flow run <feature>
pal flow approve <feature>
pal flow advance <feature> [--force-artifacts]
pal flow block <feature> --reason <reason>
pal flow replan <feature>
pal flow ship <feature> [--commit --message msg] [--push] [--create-pr]
pal flow status <feature>
pal flow watch <feature> [--follow]
pal rm <feature> [--repo repo...]
pal config init
pal config show
pal doctor
```

---

## License

MIT

## Contributing

Contributions are welcome. Please read:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
