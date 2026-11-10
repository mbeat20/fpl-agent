"""Ticket 27: `revise_plan_step` through the reasoning loop.

The Anthropic client is mocked at the same seam `test_cited_evidence.py` uses,
so these assert what the loop does with a canned model turn -- no API calls,
no database.
"""

from types import SimpleNamespace

import pytest

from fpl_coach.models import PlanRevision, Player, Team
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.reasoning.claude import ClaudeReasoning
from conftest import fake_message_response


def _tool_use(name, input_, id_):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def _response(blocks):
    return fake_message_response(blocks)


def _team_tool():
    team = Team(
        id=1,
        name="Test Team",
        players=[
            Player(id=1, name="Mohamed Salah", position="MID", club="Liverpool", price=13.5),
            Player(id=2, name="Erling Haaland", position="FWD", club="Man City", price=15.0),
        ],
    )
    return ToolSpec(name="get_my_team", description="", input_schema={}, handler=lambda: team)


def _wire(reasoning, responses):
    calls = []

    def fake_create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create
    return calls


def _revise(gameweek=5, **step_overrides):
    step = {"decision": "roll", "evidence": [{"text": "Fixtures turn in GW6."}], "risks": []}
    step.update(step_overrides)
    return _response(
        [
            _tool_use(
                "revise_plan_step",
                {"gameweek": gameweek, "note": "You were right about GW5.", "step": step},
                "revise_1",
            )
        ]
    )


@pytest.fixture
def reasoning(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return ClaudeReasoning()


def test_a_revision_is_a_terminal_and_returns_one_step(reasoning):
    _wire(reasoning, [_revise()])

    result = reasoning.reason("Why am I rolling in GW5?", [_team_tool()])

    assert isinstance(result, PlanRevision)
    assert result.gameweek == 5
    assert result.step.gameweek == 5
    assert result.note == "You were right about GW5."
    assert result.step.evidence[0].text == "Fixtures turn in GW6."


def test_the_tool_is_offered_every_turn(reasoning):
    calls = _wire(reasoning, [_revise()])

    reasoning.reason("Why am I rolling in GW5?", [_team_tool()])

    offered = {tool["name"] for tool in calls[0]["tools"]}
    assert "revise_plan_step" in offered
    assert {"submit_recommendation", "submit_plan", "respond_to_user"} <= offered


def test_a_revision_names_players_under_the_same_rule_as_a_submit(reasoning):
    """ADR-0004. A revised step lands in the same stored plan as a submitted
    one, so it must be no more able to name an unlooked-up player.

    Both sides used to carry a JSON Schema enum of the recorded ids. That made
    the tool definition change whenever a player became known, which rewrote
    the front of the cached prefix mid-conversation; the rule is now enforced
    in the builders instead, and this asserts the rule rather than the
    mechanism -- a looked-up player survives, an invented one does not, on
    both halves of the transfer."""
    _wire(
        reasoning,
        [_revise(decision="transfer", pick_player_id=1, out_player_id=9999)],
    )

    result = reasoning.reason("Change GW5", [_team_tool()])

    assert result.step.pick is not None
    assert result.step.pick.player_id == 1
    assert result.step.player_out is None


def test_a_player_the_model_was_never_shown_is_dropped(reasoning):
    """The description names the rule; the builder is what enforces it. An id
    the model was never shown must not become a pick."""
    _wire(reasoning, [_revise(pick_player_id=9999, decision="transfer")])

    result = reasoning.reason("Change GW5", [_team_tool()])

    assert result.step.pick is None


def test_a_validator_refusal_bounces_back_to_the_model_not_the_user(reasoning):
    """Ticket 22's mechanism has to cover the new terminal too. The refusal
    returns as an is_error tool_result and the model gets another turn."""
    _wire(reasoning, [_revise(), _revise(gameweek=6)])
    refusals = iter([["GW5 would leave you unable to afford GW7."], []])

    result = reasoning.reason("Change GW5", [_team_tool()], validate=lambda _: next(refusals))

    assert isinstance(result, PlanRevision)
    assert result.gameweek == 6


def test_after_a_refusal_the_model_cannot_answer_in_prose(reasoning):
    """The must_submit latch. Without it, told "this is illegal", the model
    could reply with an explanation and the user would get fluent prose where
    a corrected plan belongs."""
    calls = _wire(
        reasoning,
        [
            _revise(),
            _response([_tool_use("respond_to_user", {"text": "Fair enough, your call."}, "r1")]),
            _revise(gameweek=6),
        ],
    )
    refusals = iter([["Not legal."], []])

    result = reasoning.reason("Change GW5", [_team_tool()], validate=lambda _: next(refusals))

    assert isinstance(result, PlanRevision)
    assert result.gameweek == 6
    # The refused prose attempt came back as an error, not as the answer.
    blocked = calls[2]["messages"][-1]["content"][0]
    assert blocked["is_error"] is True
    assert "cannot reply in prose" in blocked["content"]
    assert "revise_plan_step" in blocked["content"]


def test_malformed_arguments_are_retryable_rather_than_fatal(reasoning):
    """The model API is a boundary: a garbled tool_use must not crash the
    request when the model could simply be told to try again."""
    calls = _wire(
        reasoning,
        [
            _response([_tool_use("revise_plan_step", {"note": "no gameweek"}, "bad_1")]),
            _revise(),
        ],
    )

    result = reasoning.reason("Change GW5", [_team_tool()])

    assert isinstance(result, PlanRevision)
    bounced = calls[1]["messages"][-1]["content"][0]
    assert bounced["is_error"] is True
    assert "Invalid revise_plan_step arguments" in bounced["content"]
