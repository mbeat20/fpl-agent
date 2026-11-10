"""Issue 27: a recommendation you can argue with.

Two halves, split the way the other tickets in this phase are: the transcript
and its rendering are pure and tested without a database or a model, and the
terminal contract inside `ClaudeReasoning` is tested against a mocked
Anthropic client — no network, no key spend.

The single most important test in this file is
`test_prose_is_refused_immediately_after_a_validator_rejection`. Tickets 22
and 25 both work by refusing a submit and handing the model an `is_error` to
revise against, and that mechanism only holds while the submit tools are the
*only* way out of the loop. `respond_to_user` is a third exit, so without that
latch the model can answer "that transfer is illegal because..." in prose
instead of correcting it, and the user gets fluent text where they should have
got a legal recommendation.
"""

import os
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.conversation import (
    assistant_turn,
    clear_conversation,
    load_conversation,
    render_history,
    save_conversation,
    user_turn,
)
from fpl_coach.models import (
    Alternative,
    Conversation,
    ConversationReply,
    Evidence,
    PlanStep,
    Player,
    Recommendation,
    RecommendationPick,
    Team,
    TransferPlan,
)
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.reasoning.claude import ClaudeReasoning
from conftest import fake_message_response


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _tool_use(name, input_, id_):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def _response(blocks):
    return fake_message_response(blocks)


def _team_tool():
    team = Team(
        id=1,
        name="Test Team",
        players=[
            Player(id=1, name="Player A", position="MID", club="X", price=5.0),
            Player(id=2, name="Player B", position="FWD", club="Y", price=6.0),
        ],
    )
    return ToolSpec(name="get_my_team", description="", input_schema={}, handler=lambda: team)


def _recommendation(name="Player A", player_id=1) -> Recommendation:
    return Recommendation(
        decision="captain",
        pick=RecommendationPick(player_id=player_id, player_name=name),
        confidence=0.8,
        evidence=[Evidence(text="best xG in the squad")],
        risks=["rotation"],
        alternative=Alternative(player_id=2, player_name="Player B", reason="also good"),
    )


# ---------------------------------------------------------------------------
# The transcript, rendered back to the model


def test_a_finalised_turn_is_rendered_as_a_description_not_replayed_as_a_pick():
    """ADR-0004's guarantee across turns. A rendered turn is prose ABOUT a past
    decision; it carries no player id, so nothing in the history can become
    this turn's pick. The only route to a player is still a fresh submit
    against a real enum."""
    turns = [user_turn("who should I captain?"), assistant_turn(_recommendation())]

    messages = render_history(turns)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "Player A" in messages[1]["content"]
    assert "best xG in the squad" in messages[1]["content"]
    # Prose, not structure -- there is no id to lift.
    assert "player_id" not in messages[1]["content"]


def test_a_reply_turn_renders_as_what_was_said():
    turns = [assistant_turn(ConversationReply(text="Did you mean Salah or Saka?"))]

    assert render_history(turns)[0]["content"] == "Did you mean Salah or Saka?"


def test_a_plan_turn_renders_both_sides_of_each_transfer():
    plan = TransferPlan(
        horizon="multi-week",
        confidence=0.6,
        steps=[
            PlanStep(
                gameweek=6,
                decision="transfer",
                pick=RecommendationPick(player_id=2, player_name="Player B"),
                player_out=RecommendationPick(player_id=1, player_name="Player A"),
                evidence=[Evidence(text="e")],
                risks=["r"],
            ),
            PlanStep(gameweek=5, decision="roll", evidence=[Evidence(text="e")], risks=["r"]),
        ],
    )

    content = render_history([assistant_turn(plan)])[0]["content"]

    # Sorted by gameweek, so the replayed plan reads in the order it happens.
    assert content.index("GW5") < content.index("GW6")
    assert "Player A -> Player B" in content


# ---------------------------------------------------------------------------
# The terminal contract


def _reasoning(monkeypatch, responses):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()
    calls = []

    def fake_create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create
    return reasoning, calls


def test_the_model_can_reply_instead_of_finalising(monkeypatch):
    """The whole point: an exchange no longer has to end in a decision."""
    reasoning, calls = _reasoning(
        monkeypatch,
        [_response([_tool_use("respond_to_user", {"text": "Salah or Saka?"}, "c1")])],
    )

    result = reasoning.reason("should I captain him?", [_team_tool()], validate=lambda _: [])

    assert isinstance(result, ConversationReply)
    assert result.text == "Salah or Saka?"
    assert len(calls) == 1


def test_prose_is_refused_immediately_after_a_validator_rejection(monkeypatch):
    """The escape hatch that must not exist.

    Tickets 22 and 25 refuse a submit and hand back an is_error for the model
    to revise against. That only works while the submit tools are the only way
    out. Told its move is illegal, the model here tries to explain in prose
    instead of fixing it -- and must be bounced back to a submit."""
    reasoning, calls = _reasoning(
        monkeypatch,
        [
            _response(
                [
                    _tool_use(
                        "submit_recommendation",
                        {
                            "decision": "transfer",
                            "pick_player_id": 2,
                            "confidence": 0.7,
                            "evidence": ["e"],
                            "risks": ["r"],
                        },
                        "c1",
                    )
                ]
            ),
            # The model tries to talk its way out of the refusal.
            _response([_tool_use("respond_to_user", {"text": "Well, here's why..."}, "c2")]),
            _response(
                [
                    _tool_use(
                        "submit_recommendation",
                        {
                            "decision": "roll",
                            "confidence": 0.6,
                            "evidence": ["no legal move"],
                            "risks": ["r"],
                        },
                        "c3",
                    )
                ]
            ),
        ],
    )

    def validate(result):
        if getattr(result, "decision", None) == "transfer":
            return ["Player B is a FWD and you hold no FWD to sell."]
        return []

    result = reasoning.reason("buy Player B", [_team_tool()], validate=validate)

    # The prose exit did not become the answer.
    assert isinstance(result, Recommendation)
    assert result.decision == "roll"
    # And the model was told why it could not reply, through the error channel.
    bounced = calls[2]["messages"][-1]["content"][0]
    assert bounced["is_error"] is True
    assert "cannot reply in prose" in bounced["content"]


def test_the_respond_tool_definition_never_changes_mid_conversation():
    """It used to gain a "DISABLED right now" line while the must_submit latch
    was set. That was belt-and-braces on top of the runtime refusal, and the
    braces cost more than they held up: a tool definition is the very front of
    the cached prefix, so rewriting one mid-conversation dropped the system
    prompt and the entire transcript out of cache, on the longest and most
    expensive turns. The refusal still reaches the model in the same turn as
    an is_error tool_result -- see
    test_prose_is_refused_immediately_after_a_validator_rejection."""
    assert ClaudeReasoning._build_respond_tool() == ClaudeReasoning._build_respond_tool()
    assert "DISABLED" not in ClaudeReasoning._build_respond_tool()["description"]


def test_the_respond_tool_cannot_name_a_player():
    """ADR-0004 by construction. The conversational exit has no player id
    field at all, so prose is structurally incapable of carrying a decision."""
    schema = ClaudeReasoning._build_respond_tool()["input_schema"]

    assert set(schema["properties"]) == {"text"}


def test_the_respond_tool_is_told_to_reconsider_rather_than_capitulate():
    """A coach that folds under repetition is a mirror. fpl_rules.py already
    says to give the honest assessment even when it contradicts what the
    manager wants."""
    description = ClaudeReasoning._build_respond_tool()["description"]

    assert "reconsider, not to capitulate" in description
    assert "hold that position" in description


def test_an_empty_reply_is_bounced_rather_than_returned(monkeypatch):
    reasoning, _ = _reasoning(
        monkeypatch,
        [
            _response([_tool_use("respond_to_user", {"text": ""}, "c1")]),
            _response([_tool_use("respond_to_user", {"text": "Which one?"}, "c2")]),
        ],
    )

    result = reasoning.reason("hm", [_team_tool()], validate=lambda _: [])

    assert result.text == "Which one?"


def test_prior_turns_are_replayed_to_the_model(monkeypatch):
    """The reason the ticket exists: "why not Player B?" has to reach a model
    that knows what it just said."""
    reasoning, calls = _reasoning(
        monkeypatch,
        [_response([_tool_use("respond_to_user", {"text": "Because of fixtures."}, "c1")])],
    )
    history = [user_turn("who should I captain?"), assistant_turn(_recommendation())]

    reasoning.reason("why not Player B?", [_team_tool()], validate=lambda _: [], history=history)

    sent = calls[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "who should I captain?"
    assert "Player A" in sent[1]["content"]
    assert sent[-1]["content"] == "why not Player B?"


# ---------------------------------------------------------------------------
# Persistence


def test_a_conversation_round_trips(seeded_team):
    with _conn() as conn:
        clear_conversation(conn, seeded_team)
        conversation = load_conversation(conn, seeded_team)
        conversation.turns.extend(
            [user_turn("who should I captain?"), assistant_turn(_recommendation())]
        )
        save_conversation(conn, conversation)

        reloaded = load_conversation(conn, seeded_team)

    assert [t.role for t in reloaded.turns] == ["user", "assistant"]
    # Ticket 17's cited evidence survives the round trip rather than being
    # flattened to prose in the log.
    assert reloaded.turns[1].recommendation.evidence[0].text == "best xG in the squad"
    assert reloaded.turns[1].recommendation.pick.player_name == "Player A"


def test_a_conversation_from_a_passed_gameweek_is_discarded(seeded_team):
    """Gameweek-scoped like an Instinct (ticket 13). An argument about a
    deadline that has gone would quietly re-anchor this week's reasoning on
    fixtures already played."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 30),
        )
        save_conversation(
            conn,
            Conversation(team_id=seeded_team, gameweek=29, turns=[user_turn("old argument")]),
        )

        reloaded = load_conversation(conn, seeded_team)

    assert reloaded.turns == []
    assert reloaded.gameweek == 30


def test_clearing_a_conversation_empties_it(seeded_team):
    with _conn() as conn:
        save_conversation(
            conn, Conversation(team_id=seeded_team, gameweek=None, turns=[user_turn("hi")])
        )
        clear_conversation(conn, seeded_team)

        assert load_conversation(conn, seeded_team).turns == []


# ---------------------------------------------------------------------------
# The route


class _ReplyReasoning:
    """Answers with a question rather than a decision."""

    def __init__(self):
        self.histories = []

    def reason(self, prompt, tools, system="", validate=None, history=None):
        self.histories.append(list(history or []))
        return ConversationReply(text="Did you mean Player A or Player B?")


def test_ask_returns_a_reply_and_records_the_turn(seeded_team):
    with _conn() as conn:
        clear_conversation(conn, seeded_team)

    spy = _ReplyReasoning()
    app.dependency_overrides[get_reasoning] = lambda: spy
    try:
        with TestClient(app) as client:
            response = client.post("/recommendations", json={"question": "captain?"})
            transcript = client.get("/conversation")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"text": "Did you mean Player A or Player B?"}

    turns = transcript.json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "captain?"
    assert turns[1]["text"] == "Did you mean Player A or Player B?"


def test_a_reply_is_not_logged_as_a_recommendation(seeded_team):
    """A reply is not a decision: nothing to score against an outcome
    (ADR-0012) and no plan to store as a roadmap (ADR-0016)."""
    with _conn() as conn:
        clear_conversation(conn, seeded_team)
        conn.execute("DELETE FROM recommendation_log WHERE team_id = %s", (seeded_team,))

    app.dependency_overrides[get_reasoning] = _ReplyReasoning
    try:
        with TestClient(app) as client:
            client.post("/recommendations", json={"question": "captain?"})
    finally:
        app.dependency_overrides.clear()

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM recommendation_log WHERE team_id = %s", (seeded_team,)
        )
        assert cur.fetchone()["n"] == 0


def test_the_second_question_carries_the_first_ones_history(seeded_team):
    """The failure the ticket names: every request used to be a brand-new
    conversation, so a follow-up reached a model with no idea what it had just
    said."""
    with _conn() as conn:
        clear_conversation(conn, seeded_team)

    spy = _ReplyReasoning()
    app.dependency_overrides[get_reasoning] = lambda: spy
    try:
        with TestClient(app) as client:
            client.post("/recommendations", json={"question": "who should I captain?"})
            client.post("/recommendations", json={"question": "why not the other one?"})
    finally:
        app.dependency_overrides.clear()

    assert spy.histories[0] == []
    assert [t.text for t in spy.histories[1]] == [
        "who should I captain?",
        "Did you mean Player A or Player B?",
    ]


def test_deleting_the_conversation_starts_again(seeded_team):
    with _conn() as conn:
        clear_conversation(conn, seeded_team)

    app.dependency_overrides[get_reasoning] = _ReplyReasoning
    try:
        with TestClient(app) as client:
            client.post("/recommendations", json={"question": "captain?"})
            emptied = client.delete("/conversation")
            after = client.get("/conversation")
    finally:
        app.dependency_overrides.clear()

    assert emptied.status_code == 200
    assert after.json()["turns"] == []


def test_a_remembered_instruction_does_not_enter_the_transcript(seeded_team):
    """"remember: I hate knee-jerk transfers" is a Preference (ADR-0010), not
    a turn of an argument. Putting it in the transcript would replay it as
    conversation every turn on top of the system prompt that already carries
    it."""
    with _conn() as conn:
        clear_conversation(conn, seeded_team)
        # An Instinct is tagged with the planning gameweek and log_instinct
        # refuses without one, so the route needs a gameweek to reach the
        # branch under test at all.
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 7),
        )

    app.dependency_overrides[get_reasoning] = _ReplyReasoning
    try:
        with TestClient(app) as client:
            ack = client.post(
                "/recommendations",
                json={"question": "remember: thinking Rogers to Enzo"},
            )
            transcript = client.get("/conversation")
    finally:
        app.dependency_overrides.clear()

    assert "remembered" in ack.json()
    assert transcript.json()["turns"] == []
