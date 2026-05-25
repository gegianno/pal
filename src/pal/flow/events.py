from __future__ import annotations

import json
from pathlib import Path

from .models import FlowEvent


class FlowEventLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: FlowEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), sort_keys=True))
            f.write("\n")

    def read(self) -> list[FlowEvent]:
        if not self.path.exists():
            return []
        events: list[FlowEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(FlowEvent.from_dict(json.loads(line)))
        return events
