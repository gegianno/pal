from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .base import CommandResult


class LocalCommandRunner:
    def which(self, executable: str) -> Optional[str]:
        return shutil.which(executable)

    def run(
        self,
        command: list[str],
        *,
        cwd: Optional[Path] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        completed = subprocess.run(
            command,
            check=False,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
