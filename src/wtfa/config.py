from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import platformdirs

try:
    import tomllib  # py>=3.11
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore


@dataclass
class CodexConfig:
    sandbox: str = "workspace-write"  # read-only | workspace-write | danger-full-access
    approval: str = "on-request"      # untrusted | on-failure | on-request | never
    full_auto: bool = False


@dataclass
class WSConfig:
    root: Path
    worktree_root: Path
    branch_prefix: str = "feat"
    repos: list[str] = field(default_factory=list)  # optional allowlist
    editor: str = ""                                # cursor | code | "" (auto)
    codex: CodexConfig = field(default_factory=CodexConfig)

    @property
    def ws_local_path(self) -> Path:
        return self.root / ".wtfa.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def global_config_path() -> Path:
    cfg_dir = Path(platformdirs.user_config_dir("wtfa", appauthor=False))
    return cfg_dir / "config.toml"


def load_config(
    root: Path,
    cli_overrides: Optional[dict[str, Any]] = None,
) -> WSConfig:
    """
    Load config with precedence: CLI overrides > local .wtfa.toml > global config.
    """
    cli_overrides = cli_overrides or {}

    # Start with defaults
    cfg = WSConfig(
        root=root,
        worktree_root=Path("_wt"),
    )

    # Apply global, then local
    for p in [global_config_path(), cfg.ws_local_path]:
        d = _read_toml(p)
        if not d:
            continue
        _apply_dict(cfg, d)

    # Finally apply CLI overrides
    _apply_dict(cfg, cli_overrides)

    # Normalize paths
    cfg.root = Path(cfg.root).expanduser().resolve()
    wt_root = Path(cfg.worktree_root).expanduser()
    cfg.worktree_root = (cfg.root / wt_root).resolve() if not wt_root.is_absolute() else wt_root.resolve()

    return cfg


def _apply_dict(cfg: WSConfig, d: dict[str, Any]) -> None:
    if "root" in d:
        cfg.root = Path(d["root"])
    if "worktree_root" in d:
        cfg.worktree_root = Path(d["worktree_root"])
    if "branch_prefix" in d:
        cfg.branch_prefix = str(d["branch_prefix"])
    if "repos" in d and isinstance(d["repos"], list):
        cfg.repos = [str(x) for x in d["repos"]]
    if "editor" in d:
        cfg.editor = str(d["editor"])

    codex = d.get("codex")
    if isinstance(codex, dict):
        if "sandbox" in codex:
            cfg.codex.sandbox = str(codex["sandbox"])
        if "approval" in codex:
            cfg.codex.approval = str(codex["approval"])
        if "full_auto" in codex:
            cfg.codex.full_auto = bool(codex["full_auto"])
