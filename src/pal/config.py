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
class LocalFilesRepoConfig:
    paths: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)


@dataclass
class LocalFilesConfig:
    enabled: bool = False
    overwrite: bool = False
    # Paths are repo-relative (e.g. ".env", "backend/.env.prod.local").
    # `paths` applies to all repos; `repos` overrides/adds per repo name.
    paths: list[str] = field(default_factory=list)
    # Globs are repo-relative (e.g. ".env*", "**/.env*", "**/.npmrc").
    patterns: list[str] = field(default_factory=list)
    repos: dict[str, LocalFilesRepoConfig] = field(default_factory=dict)


@dataclass
class PalConfig:
    root: Path
    worktree_root: Path
    branch_prefix: str = "feat"
    repos: list[str] = field(default_factory=list)  # optional allowlist
    editor: str = ""                                # cursor | code | "" (auto)
    codex: CodexConfig = field(default_factory=CodexConfig)
    local_files: LocalFilesConfig = field(default_factory=LocalFilesConfig)

    @property
    def local_config_path(self) -> Path:
        return self.root / ".pal.toml"

    # Backward-compatible alias (older name used `ws_*`).
    @property
    def ws_local_path(self) -> Path:  # pragma: no cover
        return self.local_config_path


# Backward-compatible aliases.
WtfaConfig = PalConfig
WSConfig = PalConfig


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def global_config_path() -> Path:
    cfg_dir = Path(platformdirs.user_config_dir("pal", appauthor=False))
    return cfg_dir / "config.toml"


def legacy_global_config_path() -> Path:
    cfg_dir = Path(platformdirs.user_config_dir("wtfa", appauthor=False))
    return cfg_dir / "config.toml"


def load_config(
    root: Path,
    cli_overrides: Optional[dict[str, Any]] = None,
) -> PalConfig:
    """
    Load config with precedence: CLI overrides > local .pal.toml > global config.
    """
    cli_overrides = cli_overrides or {}

    # Start with defaults
    cfg = PalConfig(
        root=root,
        worktree_root=Path("_wt"),
    )

    # Apply global (new then legacy), then local (new then legacy)
    for p in [
        global_config_path(),
        legacy_global_config_path(),
        cfg.local_config_path,
        (cfg.root / ".wtfa.toml"),
    ]:
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


def _apply_dict(cfg: PalConfig, d: dict[str, Any]) -> None:
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

    local_files = d.get("local_files")
    if isinstance(local_files, dict):
        if "enabled" in local_files:
            cfg.local_files.enabled = bool(local_files["enabled"])
        if "overwrite" in local_files:
            cfg.local_files.overwrite = bool(local_files["overwrite"])
        if "paths" in local_files and isinstance(local_files["paths"], list):
            cfg.local_files.paths = [str(x) for x in local_files["paths"]]
        if "patterns" in local_files and isinstance(local_files["patterns"], list):
            cfg.local_files.patterns = [str(x) for x in local_files["patterns"]]

        repos = local_files.get("repos")
        if isinstance(repos, dict):
            parsed: dict[str, LocalFilesRepoConfig] = {}
            for repo_name, value in repos.items():
                if isinstance(value, list):
                    # Back-compat: list means `paths`.
                    parsed[str(repo_name)] = LocalFilesRepoConfig(paths=[str(x) for x in value])
                elif isinstance(value, dict):
                    repo_cfg = LocalFilesRepoConfig()
                    if "paths" in value and isinstance(value["paths"], list):
                        repo_cfg.paths = [str(x) for x in value["paths"]]
                    if "patterns" in value and isinstance(value["patterns"], list):
                        repo_cfg.patterns = [str(x) for x in value["patterns"]]
                    parsed[str(repo_name)] = repo_cfg
            cfg.local_files.repos = parsed
