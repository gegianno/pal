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
pal flow validate dev-complex
pal flow start feat-auth --workflow dev-complex
pal flow render feat-auth
pal flow execute feat-auth
pal flow approve feat-auth
pal flow advance feat-auth
pal flow block feat-auth --reason "implementation hit a dependency issue"
pal flow replan feat-auth
pal flow status feat-auth
pal flow watch feat-auth
```

Provider execution is still explicit and safe by default: `pal flow start` records durable run state
and provider preflight metadata, but only runs a local headless agent when `--headless --prompt ...`
is passed.

Phase progression is explicit too. `advance` follows workflow transitions, `approve` satisfies
`requires_approval: true` gates, and `block`/`replan` records replanning loops without hiding them
inside agent output.

`pal flow render <feature>` compiles the current phase into durable
`.pal/runs/<run-id>/phase/<phase>/brief.md` and `brief.json` artifacts. The brief includes run
state, the active policy, resolved phase agents, required artifacts, recent events, approvals,
blockers, and small provider-specific guidance for the selected provider.

`pal flow execute <feature>` renders the current phase, runs each rendered phase agent through the
selected provider's local headless adapter, and records per-agent prompts, stdout/stderr logs, and
manifests under `.pal/runs/<run-id>/phase/<phase>/executions/<execution-id>/`. Execution records are
durable, but V1 execution does not auto-advance the workflow; `approve`, `advance`, `block`, and
`replan` remain explicit state changes.

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
pal flow providers
pal flow validate [workflow]
pal flow start <feature> [--workflow workflow]
pal flow render <feature>
pal flow execute <feature>
pal flow approve <feature>
pal flow advance <feature>
pal flow block <feature> --reason <reason>
pal flow replan <feature>
pal flow status <feature>
pal flow watch <feature>
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
