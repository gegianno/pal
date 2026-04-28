from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .flow_models import FlowRun


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


class FlowProvider(Protocol):
    name: str
    start: Callable[[FlowRun], ProviderResult]
