from __future__ import annotations

import sys

from pal.flow.providers.command import LocalCommandRunner, _output_text


def test_local_command_runner_which_and_run() -> None:
    runner = LocalCommandRunner()

    assert runner.which("definitely-not-a-real-pal-test-executable") is None
    result = runner.run(["/bin/sh", "-c", "printf out; printf err >&2; exit 3"])

    assert result.returncode == 3
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_local_command_runner_reports_missing_executable() -> None:
    result = LocalCommandRunner().run(["definitely-not-a-real-pal-test-executable"])

    assert result.returncode == 127
    assert "Executable not found" in result.stderr


def test_local_command_runner_closes_stdin_by_default() -> None:
    result = LocalCommandRunner().run(
        ["/bin/sh", "-c", "if read line; then printf open; else printf closed; fi"],
    )

    assert result.returncode == 0
    assert result.stdout == "closed"


def test_local_command_runner_passes_input_text() -> None:
    result = LocalCommandRunner().run(
        ["/bin/sh", "-c", "cat"],
        input_text="prompt over stdin",
    )

    assert result.returncode == 0
    assert result.stdout == "prompt over stdin"


def test_local_command_runner_reports_timeout() -> None:
    result = LocalCommandRunner().run(
        [sys.executable, "-c", "import time; time.sleep(1)"],
        timeout=0,
    )

    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_output_text_normalizes_timeout_payloads() -> None:
    assert _output_text(None) == ""
    assert _output_text(b"abc") == "abc"
    assert _output_text("abc") == "abc"
