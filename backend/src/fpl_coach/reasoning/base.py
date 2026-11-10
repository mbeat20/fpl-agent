from dataclasses import dataclass
from typing import Any, Callable, Protocol

from fpl_coach.models import (
    ConversationReply,
    ConversationTurn,
    PlanRevision,
    Recommendation,
    TransferPlan,
)


# Issue 22: an invariant the finished answer must satisfy, checked by the
# caller (which has the database) rather than by the reasoning implementation
# (which does not). Returns violation messages -- empty means the result
# stands.
#
# This lives on the interface rather than inside ClaudeReasoning because it is
# a guarantee about the OUTPUT, not a detail of one provider: ADR-0004 already
# says the pick is built from an authoritative lookup rather than from model
# text, and "and it must be a legal move" is the same principle carried one
# step further. A Reasoning implementation that cannot enforce it -- the test
# double -- is free to ignore the argument.
# Issue 27: a PlanRevision is validated too, and by the same callable --
# the validator merges it into the live roadmap first, so what gets checked
# is the whole plan the revision would produce, not the one step in
# isolation. A step that is legal on its own can spend money a later step
# needed, which is exactly what the cumulative walk exists to catch.
ResultValidator = Callable[["Recommendation | TransferPlan | PlanRevision"], list[str]]


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

    Issue 27 adds a third possible return, ConversationReply: the model
    talking rather than finalising -- asking which of two players the user
    meant, or engaging with an objection. `history` is the prior transcript,
    empty for a fresh exchange. An implementation that cannot converse is free
    to ignore it and always finalise; the caller handles all three shapes.
    """

    def reason(
        self,
        prompt: str,
        tools: list[ToolSpec],
        system: str = "",
        validate: "ResultValidator | None" = None,
        history: "list[ConversationTurn] | None" = None,
    ) -> Recommendation | TransferPlan | ConversationReply | PlanRevision: ...
