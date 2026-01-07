# wtfa — Feature workspaces for multi-repo git worktrees

`wtfa` helps you create **feature-scoped “sandboxes”** across multiple repos using **Git worktrees**.
It’s designed for running **multiple Codex instances locally** without stepping on each other.

It also generates a **VS Code / Cursor multi-root workspace** per feature so you can review changes easily.

---

## Why this exists

- Git worktrees are **per repository**, but features often span multiple repos.  
- Coding agents (Codex, etc.) work best when their writable surface area is **small**.
- You want **projects-level visibility**, but **feature-level isolation**.

`wtfa` gives you a simple model:

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
wtfa doctor
wtfa repos

wtfa new feat-auth repo1 repo3
wtfa open feat-auth       # opens a multi-root workspace in Cursor/VS Code (if detected)

wtfa codex feat-auth      # interactive Codex, write-limited to _wt/feat-auth
wtfa exec feat-auth "Implement X and run tests"
```

If later you realize you need another repo:

```bash
wtfa add feat-auth repo4
```

Cleanup:

```bash
wtfa rm feat-auth         # removes all worktrees under _wt/feat-auth
# or remove a subset:
wtfa rm feat-auth --repo repo1 --repo repo3
```

---

## Codex safety defaults (important)

When you run Codex via `wtfa codex` / `wtfa exec`, `wtfa` always launches it with:

- `--cd <feature_workspace_dir>` to set the workspace root  
- `--sandbox workspace-write` to restrict writes to that workspace directory  

These are **per-invocation flags**, so your global `~/.codex/config.toml` defaults remain unchanged.  
See Codex CLI flags (`--cd`, `--sandbox`, `--full-auto`) in the official reference.

---

## Configuration (no hard-coded folder names)

`wtfa` is intentionally not tied to a specific directory layout.

### Config file discovery

`wtfa` loads config in this precedence order:

1. CLI flags
2. Local config: `<root>/.wtfa.toml`
3. Global config (XDG): `~/.config/wtfa/config.toml` (or platform equivalent)

To create a local config:

```bash
wtfa config init
```

### Example `.wtfa.toml`

```toml
# Root folder that contains your repos (defaults to current directory)
root = "."

# Where feature workspaces live (defaults to "<root>/_wt")
worktree_root = "_wt"

# How branches are named inside each repo worktree
branch_prefix = "feat"

# Repos discovery (optional):
# If you set this, wtfa will only consider these as repos.
repos = ["repo1", "repo2", "repo3", "repo4", "repo5"]

# Prefer "cursor" or "code" (auto-detected if omitted)
editor = "cursor"

[codex]
sandbox = "workspace-write"   # read-only | workspace-write | danger-full-access
approval = "on-request"       # untrusted | on-failure | on-request | never
full_auto = false             # if true, passes --full-auto
```

---

## UX tips

### Shell completions

Typer supports built-in completion installers:

```bash
wtfa --install-completion
# or:
wtfa --show-completion
```

### Keep a “global overview” editor window

You can still open your big `projects/` folder in Cursor for global visibility, while also opening
feature workspaces (`wtfa open <feature>`) for focused review.

---

## Commands

```bash
wtfa --help

wtfa repos
wtfa new <feature> <repo...>
wtfa add <feature> <repo...>
wtfa ls
wtfa status <feature>
wtfa open <feature>
wtfa codex <feature>
wtfa exec <feature> "<prompt>"
wtfa rm <feature> [--repo repo...]
wtfa config init
wtfa config show
wtfa doctor
```

---

## License

MIT
