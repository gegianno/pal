from __future__ import annotations

from pathlib import Path

import pytest

from pal.flow_models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow_store import LocalFlowStore


def _run(feature: str = "feat", run_id: str = "run_1") -> FlowRun:
    return FlowRun(
        run_id=run_id,
        feature=feature,
        mode="routine",
        repos=["api"],
        current_phase=FlowPhase.EXPLORE,
        status=FlowStatus.RUNNING,
        policies={"explore": FlowPolicy.AUTONOMOUS},
        artifact_root=f"/tmp/{feature}/.pal/artifacts",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )


def _event(run_id: str = "run_1") -> FlowEvent:
    return FlowEvent(
        id="evt_1",
        run_id=run_id,
        type="flow.run.started",
        timestamp="2026-04-27T00:00:00Z",
        phase=FlowPhase.EXPLORE,
        actor="pal",
    )


def test_store_creates_run_layout_state_latest_and_events(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()

    store.create_run(run)
    store.append_event(run.feature, run.run_id, _event(run.run_id))

    assert store.feature_dir("feat") == tmp_path / "_wt" / "feat"
    assert store.pal_dir("feat") == tmp_path / "_wt" / "feat" / ".pal"
    assert store.runs_dir("feat") == tmp_path / "_wt" / "feat" / ".pal" / "runs"
    assert store.run_dir("feat", "run_1").is_dir()
    assert (store.run_dir("feat", "run_1") / "providers").is_dir()
    assert (store.run_dir("feat", "run_1") / "latest").is_dir()
    assert store.latest_run_id("feat") == "run_1"
    assert store.resolve_run_id("feat", "explicit") == "explicit"
    assert store.resolve_run_id("feat", None) == "run_1"
    assert store.load_state("feat", "run_1") == run
    assert store.load_run("feat") == run
    assert store.read_events("feat") == [_event(run.run_id)]
    assert store.read_events("feat", "run_1") == [_event(run.run_id)]


def test_store_save_state_updates_existing_run(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    updated = {
        **run.to_dict(),
        "status": FlowStatus.COMPLETED.value,
        "updated_at": "2026-04-27T00:01:00Z",
    }

    store.create_run(run)
    store.save_state(FlowRun.from_dict(updated))

    assert store.load_run("feat").status == FlowStatus.COMPLETED


def test_store_raises_when_latest_run_is_missing(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")

    with pytest.raises(FileNotFoundError, match="No flow runs found"):
        store.latest_run_id("missing")
