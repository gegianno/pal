from __future__ import annotations

from pathlib import Path

from pal.flow.events import FlowEventLog
from pal.flow.models import FlowEvent, FlowPhase


def _event(event_id: str = "evt_1") -> FlowEvent:
    return FlowEvent(
        id=event_id,
        run_id="run_1",
        type="flow.run.started",
        timestamp="2026-04-27T00:00:00Z",
        phase=FlowPhase.EXPLORE,
        actor="pal",
        payload={"ok": True},
    )


def test_event_log_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert FlowEventLog(tmp_path / "events.jsonl").read() == []


def test_event_log_appends_and_reads_jsonl(tmp_path: Path) -> None:
    log = FlowEventLog(tmp_path / "nested" / "events.jsonl")
    log.append(_event("evt_1"))
    log.append(_event("evt_2"))

    assert [event.id for event in log.read()] == ["evt_1", "evt_2"]


def test_event_log_ignores_blank_lines(tmp_path: Path) -> None:
    log = FlowEventLog(tmp_path / "events.jsonl")
    log.append(_event())
    log.path.write_text(f"{log.path.read_text(encoding='utf-8')}\n", encoding="utf-8")

    assert len(log.read()) == 1
