from __future__ import annotations

import json
from pathlib import Path

import pytest

from pal.flow.models import FlowEvent, FlowPhase, FlowPolicy, FlowRun, FlowStatus
from pal.flow.store import LocalFlowStore


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


def test_store_writes_run_json_and_latest_provider_outputs(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)

    json_path = store.write_run_json(run, "provider-auth.json", {"fake": {"status": "ok"}})
    output_paths = store.write_latest_output(
        run,
        provider="fake",
        stdout='{"event":"ok"}\n',
        stderr="warning\n",
    )

    assert json_path == store.run_dir("feat", "run_1") / "provider-auth.json"
    assert store.read_run_json("feat", "run_1", "provider-auth.json") == {"fake": {"status": "ok"}}
    assert Path(output_paths["stdout"]).read_text(encoding="utf-8") == '{"event":"ok"}\n'
    assert Path(output_paths["stderr"]).read_text(encoding="utf-8") == "warning\n"
    assert Path(output_paths["provider_log"]) == store.provider_log_path("feat", "run_1", "fake")
    assert store.latest_output_dir("feat", "run_1") == store.run_dir("feat", "run_1") / "latest"


def test_store_writes_phase_brief_artifacts(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)

    paths = store.write_phase_brief(
        run,
        phase="explore",
        markdown="# Brief\n",
        data={"phase": {"id": "explore"}},
    )

    assert store.phase_dir("feat", "run_1", "explore") == (
        store.run_dir("feat", "run_1") / "phase" / "explore"
    )
    assert Path(paths["markdown"]).read_text(encoding="utf-8") == "# Brief\n"
    parsed = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert parsed["phase"]["id"] == "explore"
    assert parsed["paths"] == paths


def test_store_writes_phase_execution_artifacts(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)

    paths = store.write_phase_execution(
        run,
        phase="explore",
        execution_id="exec_1",
        prompt="# Prompt\n",
        stdout="ok\n",
        stderr="warn\n",
        manifest={"execution_id": "exec_1", "status": "completed"},
    )

    execution_dir = store.phase_execution_dir("feat", "run_1", "explore", "exec_1")
    assert execution_dir == store.phase_dir("feat", "run_1", "explore") / "executions" / "exec_1"
    assert Path(paths["prompt"]).read_text(encoding="utf-8") == "# Prompt\n"
    assert Path(paths["stdout"]).read_text(encoding="utf-8") == "ok\n"
    assert Path(paths["stderr"]).read_text(encoding="utf-8") == "warn\n"
    parsed = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    assert parsed["execution_id"] == "exec_1"
    assert parsed["paths"] == paths


def test_store_writes_and_reads_hook_results(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    run = _run()
    store.create_run(run)

    assert store.read_hook_results("feat") == []
    store.append_hook_result(run, {"hook": "notify", "returncode": 0})

    assert store.hooks_path("feat", "run_1") == store.run_dir("feat", "run_1") / "hooks.jsonl"
    assert store.read_hook_results("feat") == [{"hook": "notify", "returncode": 0}]


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


@pytest.mark.parametrize("feature", ["../escape", "/tmp/escape", ".", "feat/nested"])
def test_store_rejects_feature_names_that_escape_workspace(
    tmp_path: Path,
    feature: str,
) -> None:
    store = LocalFlowStore(tmp_path / "_wt")

    with pytest.raises(ValueError, match="Feature name"):
        store.feature_dir(feature)


@pytest.mark.parametrize("run_id", ["../evil", "/tmp/evil", ".", "run/nested"])
def test_store_rejects_run_ids_that_escape_run_storage(
    tmp_path: Path,
    run_id: str,
) -> None:
    store = LocalFlowStore(tmp_path / "_wt")

    with pytest.raises(ValueError, match="Run ID"):
        store.resolve_run_id("feat", run_id)


def test_store_rejects_tampered_latest_run_id(tmp_path: Path) -> None:
    store = LocalFlowStore(tmp_path / "_wt")
    latest_path = store.latest_path("feat")
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text("/tmp/evil\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Run ID"):
        store.latest_run_id("feat")
