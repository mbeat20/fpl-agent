"""Issue 27: the live exchange, so a Recommendation can be argued with.

Every request to `/recommendations` used to be a brand-new conversation. Ask
"who should I captain?", get a full answer with cited evidence, then ask "why
not Haaland?" — and the second question reached a model with no idea it had
just said anything. That is the specific feeling of being talked at rather
than advised, and `preferences.md` is explicit that the final call must be
justifiable as the user's *own* reasoning. You cannot own a decision you were
unable to question.

Two design decisions here are load-bearing.

**The transcript stores what was said, not how it was worked out.** A turn
holds the user's words or the model's reply, and — for a finalised turn — the
whole `Recommendation` or `TransferPlan` object. It does NOT store the
Anthropic message list: tool calls and tool results are working, not
conversation. Persisting them would grow without bound, would mean serialising
provider SDK objects into our own schema (ADR-0003 keeps the provider behind
the interface), and would spend tokens every turn replaying lookups the model
can redo more cheaply. The cost is that the model cannot see its own earlier
tool results and may re-fetch; prompt caching already absorbs most of that.

**A finalised turn keeps its structure.** A Recommendation is stored as a
Recommendation, not flattened to prose. Ticket 17 went to some trouble to make
evidence lines carry checkable citations, and turning them into a paragraph
inside a chat log would throw exactly that away.
"""

from datetime import datetime, timezone

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import (
    Conversation,
    ConversationReply,
    ConversationTurn,
    Recommendation,
    TransferPlan,
)

_SELECT = "SELECT team_id, gameweek, turns FROM conversations WHERE team_id = %s"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_conversation(conn: Connection, team_id: int) -> Conversation:
    """The live conversation, or an empty one.

    A conversation stored against an earlier gameweek is dropped rather than
    continued — the same "invisible once the gameweek moves on" rule
    `get_instincts` (ticket 13) and `get_cached_suggestion` (ticket 15) both
    follow. It is discarded lazily, on read, rather than by a cleanup job:
    ADR-0002 has no scheduler to run one.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    with conn.cursor() as cur:
        cur.execute(_SELECT, (team_id,))
        row = cur.fetchone()

    if row is None:
        return Conversation(team_id=team_id, gameweek=planning, turns=[])

    if planning is not None and row["gameweek"] is not None and row["gameweek"] != planning:
        return Conversation(team_id=team_id, gameweek=planning, turns=[])

    try:
        turns = [ConversationTurn(**turn) for turn in (row["turns"] or [])]
    except ValidationError:
        # A transcript written under an older turn shape. Unlike a roadmap
        # version (ticket 25) this is not history worth preserving — it is a
        # working exchange — so the honest failure is to start fresh rather
        # than to half-read it and feed the model a partial argument.
        turns = []

    return Conversation(team_id=team_id, gameweek=row["gameweek"] or planning, turns=turns)


def save_conversation(conn: Connection, conversation: Conversation) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (team_id, gameweek, turns) VALUES (%s, %s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek, "
            "turns = EXCLUDED.turns, updated_at = now()",
            (
                conversation.team_id,
                conversation.gameweek,
                Jsonb([turn.model_dump(mode="json") for turn in conversation.turns]),
            ),
        )
    conn.commit()


def clear_conversation(conn: Connection, team_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM conversations WHERE team_id = %s", (team_id,))
    conn.commit()


def user_turn(text: str) -> ConversationTurn:
    return ConversationTurn(role="user", text=text, created_at=_now())


def assistant_turn(
    result: Recommendation | TransferPlan | ConversationReply,
    note: str | None = None,
) -> ConversationTurn:
    """One assistant entry in the transcript.

    `note` (issue 27) is set only for a revised plan, and is the sentence
    saying what changed about the one step the user questioned. It rides
    alongside the plan rather than replacing it: the user needs both the
    revised plan rendered properly and the reason it differs from the one
    they were looking at a moment ago. A turn already allows `text` and
    `plan` together, so this needs no new shape.
    """
    if isinstance(result, ConversationReply):
        return ConversationTurn(role="assistant", text=result.text, created_at=_now())
    if isinstance(result, TransferPlan):
        return ConversationTurn(role="assistant", text=note, plan=result, created_at=_now())
    return ConversationTurn(role="assistant", recommendation=result, created_at=_now())


# ---------------------------------------------------------------------------
# Rendering the transcript back to the model


def _render_recommendation(recommendation: Recommendation) -> str:
    parts = [f"I recommended: {recommendation.decision}"]
    if recommendation.pick is not None:
        parts.append(f"— {recommendation.pick.player_name}")
    parts.append(f"(confidence {recommendation.confidence:.2f}).")
    if recommendation.evidence:
        parts.append("Evidence: " + " ".join(e.text for e in recommendation.evidence))
    if recommendation.risks:
        parts.append("Risks: " + " ".join(recommendation.risks))
    if recommendation.alternative is not None:
        parts.append(
            f"Alternative offered: {recommendation.alternative.player_name} — "
            f"{recommendation.alternative.reason}"
        )
    return " ".join(parts)


def _render_plan(plan: TransferPlan) -> str:
    steps = []
    for step in sorted(plan.steps, key=lambda s: s.gameweek):
        line = f"GW{step.gameweek}: {step.decision}"
        if step.player_out is not None and step.pick is not None:
            line += f" {step.player_out.player_name} -> {step.pick.player_name}"
        elif step.pick is not None:
            line += f" {step.pick.player_name}"
        steps.append(line)
    return (
        f"I gave a {plan.horizon} plan (confidence {plan.confidence:.2f}): "
        + "; ".join(steps)
        + "."
    )


def render_history(turns: list[ConversationTurn]) -> list[dict]:
    """The prior transcript as plain Anthropic messages.

    Each finalised turn is rendered to a summary sentence rather than replayed
    as a tool call. That is what keeps this honest under ADR-0004: a rendered
    turn is a *description* of a past decision, and the model reading it
    cannot turn it back into a pick — the only way to name a player is still a
    fresh submit tool call against a real enum. It also means a stored turn
    can never smuggle a player id the current request has not looked up.
    """
    messages: list[dict] = []
    for turn in turns:
        if turn.role == "user":
            messages.append({"role": "user", "content": turn.text or ""})
            continue
        if turn.recommendation is not None:
            content = _render_recommendation(turn.recommendation)
        elif turn.plan is not None:
            # Issue 27: a revised plan's note comes first, so the next turn
            # reads "I changed GW5 because ..." before the plan itself.
            # Without it the model sees only the new plan and has no record
            # that it already conceded a point -- and would be liable to
            # re-argue the step it just revised.
            content = _render_plan(turn.plan)
            if turn.text:
                content = f"{turn.text} {content}"
        else:
            content = turn.text or ""
        messages.append({"role": "assistant", "content": content})
    return messages
