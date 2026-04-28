from __future__ import annotations

from pal.flow.providers.command import LocalCommandRunner


def test_local_command_runner_which_and_run() -> None:
    runner = LocalCommandRunner()

    assert runner.which("definitely-not-a-real-pal-test-executable") is None
    result = runner.run(["/bin/sh", "-c", "printf out; printf err >&2; exit 3"])

    assert result.returncode == 3
    assert result.stdout == "out"
    assert result.stderr == "err"
