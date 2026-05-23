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
        input_text: Optional[str] = None,
    ) -> CommandResult:
        try:
            stdin_kwargs: dict[str, object]
            if input_text is None:
                stdin_kwargs = {"stdin": subprocess.DEVNULL}
            else:
                stdin_kwargs = {"input": input_text}
            completed = subprocess.run(
                command,
                check=False,
                cwd=str(cwd) if cwd else None,
                text=True,
                capture_output=True,
                timeout=timeout,
                **stdin_kwargs,
            )
            return CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except FileNotFoundError:
            return CommandResult(
                returncode=127,
                stderr=f"Executable not found: {command[0] if command else '(empty command)'}",
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _output_text(exc.stdout)
            stderr = _output_text(exc.stderr)
            timeout_detail = f"Command timed out after {timeout} seconds."
            return CommandResult(
                returncode=124,
                stdout=stdout,
                stderr=f"{stderr}\n{timeout_detail}".strip(),
            )


def _output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
