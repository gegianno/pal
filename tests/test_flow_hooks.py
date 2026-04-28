from __future__ import annotations

import sys
from pathlib import Path

from pal.flow.hooks import (
    FlowHook,
    FlowHookDispatcher,
    HookCommandResult,
    HookResult,
    LocalHookRunner,
)
from pal.flow.models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.store import LocalFlowStore


class FakeHookRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[list[str], Path | None, dict[str, str] | None, int | None]] = []

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> HookCommandResult:
        self.calls.append((command, cwd, env, timeout))
        if self.fail:
            raise RuntimeError("hook exploded")
        return HookCommandResult(returncode=0, stdout="notified\n")


def _run() -> FlowRun:
    return FlowRun(
        run_id="run_1",
        feature="feat",
        mode="routine",
        repos=[],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies={"explore": FlowPolicy.AUTONOMOUS},
        artifact_root="/tmp/artifacts",
        created_at="now",
        updated_at="now",
    )


def _event(event_type: str = "flow.run.started") -> FlowEvent:
    return FlowEvent(
        id="evt_1",
        run_id="run_1",
        type=event_type,
        timestamp="now",
        phase=FlowPhase.EXPLORE,
        actor="pal",
    )


def test_flow_hook_dispatcher_runs_matching_hooks_and_records_results(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)
    runner = FakeHookRunner()
    dispatcher = FlowHookDispatcher(
        hooks=[
            FlowHook(name="all", command=["notify"], events=["*"]),
            FlowHook(name="skip", command=["skip"], events=["other"]),
        ],
        runner=runner,
        timeout=5,
    )

    results = dispatcher.dispatch(event=_event(), run=run, store=store)

    assert len(results) == 1
    assert results[0].hook == "all"
    assert runner.calls[0][0] == ["notify"]
    assert runner.calls[0][1] == store.feature_dir("feat")
    assert runner.calls[0][2]["PAL_FLOW_EVENT_TYPE"] == "flow.run.started"
    assert runner.calls[0][3] == 5
    assert store.read_hook_results("feat", "run_1")[0]["stdout"] == "notified\n"


def test_flow_hook_dispatcher_records_runner_exceptions(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)
    dispatcher = FlowHookDispatcher(
        hooks=[FlowHook(name="bad", command=["missing"])],
        runner=FakeHookRunner(fail=True),
    )

    results = dispatcher.dispatch(event=_event(), run=run, store=store)

    assert results[0].returncode == 127
    assert "hook exploded" in results[0].stderr
    assert store.read_hook_results("feat")[0]["returncode"] == 127


def test_hook_result_serializes_command_output() -> None:
    result = HookResult(
        hook="notify",
        event_id="evt_1",
        event_type="flow.phase.advanced",
        command=["notify"],
        returncode=0,
        stdout="ok",
        stderr="",
    )

    assert result.to_dict()["event_type"] == "flow.phase.advanced"


def test_local_hook_runner_executes_subprocess() -> None:
    result = LocalHookRunner().run(
        [sys.executable, "-c", "print('hook-ok')"],
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "hook-ok\n"
