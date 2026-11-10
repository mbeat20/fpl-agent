from dataclasses import dataclass
from typing import Any, Callable, Protocol

from fpl_coach.models import Recommendation, TransferPlan


@dataclass(frozen=True)
class ToolSpec:
    """A tool a Reasoning implementation may call while producing a Recommendation."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]


class Reasoning(Protocol):
    """ADR-0003: the reasoning model sits behind this interface, not a hardcoded provider call.

    Issue 12: returns a TransferPlan instead of a Recommendation when the
    question's Horizon (CONTEXT.md) is longer than the current gameweek --
    which of the two it returns is itself inferred live from the question,
    never a caller-supplied parameter.
    """

    def reason(
        self, prompt: str, tools: list[ToolSpec], system: str = ""
    ) -> Recommendation | TransferPlan: ...
