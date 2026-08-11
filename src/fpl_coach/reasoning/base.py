from dataclasses import dataclass
from typing import Any, Callable, Protocol

from fpl_coach.models import Recommendation


@dataclass(frozen=True)
class ToolSpec:
    """A tool a Reasoning implementation may call while producing a Recommendation."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]


class Reasoning(Protocol):
    """ADR-0003: the reasoning model sits behind this interface, not a hardcoded provider call."""

    def reason(
        self, prompt: str, tools: list[ToolSpec], system: str = ""
    ) -> Recommendation: ...
