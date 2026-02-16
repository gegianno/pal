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
pal run <feature> <agent> [agent args...]
pal plan <feature> <agent> [agent args...]
pal implement <feature> <agent> [agent args...]
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
