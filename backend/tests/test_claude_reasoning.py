"""Unit tests for ClaudeReasoning's own control flow, with the Anthropic
client mocked out -- no network calls, no API key spend. Everything else
about ClaudeReasoning (does Claude actually infer the right Horizon, cite
real evidence, etc.) requires the real model and lives in
test_recommendations_integration.py instead."""

from types import SimpleNamespace

from fpl_coach.models import Player, Team
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
            Player(id=1, name="Player A", position="MID", club="X", price=5.0),
            Player(id=2, name="Player B", position="FWD", club="Y", price=6.0),
        ],
    )
    return ToolSpec(name="get_my_team", description="", input_schema={}, handler=lambda: team)


def test_recovers_from_a_malformed_submit_recommendation_call(monkeypatch):
    """The model API is a boundary: a garbled tool_use (missing/wrong-typed
    args -- observed in practice against the real API) must feed back as a
    retryable tool error, not crash the whole request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "captain",
                        "pick_player_id": 1,
                        "confidence": 0.5,
                        "evidence": "not a list",  # malformed: should be list[str]
                        "risks": ["r"],
                        "alternative_player_id": 2,
                        "alternative_reason": "x",
                    },
                    "call_1",
                )
            ]
        ),
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "captain",
                        "pick_player_id": 1,
                        "confidence": 0.5,
                        "evidence": ["e"],
                        "risks": ["r"],
                        "alternative_player_id": 2,
                        "alternative_reason": "x",
                    },
                    "call_2",
                )
            ]
        ),
    ]
    calls = []

    def fake_create(**kwargs):
        # messages is a mutable list `reason()` keeps appending to across
        # turns -- snapshot it now, or a later turn's mutation would be
        # visible retroactively in this call's recorded kwargs.
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.pick.player_id == 1
    assert len(calls) == 2
    # the retry turn must carry the error back to Claude as a tool_result
    retry_messages = calls[1]["messages"]
    error_result = retry_messages[-1]["content"][0]
    assert error_result["tool_use_id"] == "call_1"
    assert error_result["is_error"] is True


def test_recovers_from_a_submit_recommendation_missing_a_required_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        # `decision` missing entirely. Deliberately not
                        # alternative_player_id: that key is optional by
                        # design now, so omitting it is valid input rather
                        # than the malformed call this test needs.
                        "pick_player_id": 1,
                        "confidence": 0.5,
                        "evidence": ["e"],
                        "risks": ["r"],
                        "alternative_reason": "x",
                    },
                    "call_1",
                )
            ]
        ),
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "captain",
                        "pick_player_id": 2,
                        "confidence": 0.5,
                        "evidence": ["e"],
                        "risks": ["r"],
                        "alternative_player_id": 1,
                        "alternative_reason": "x",
                    },
                    "call_2",
                )
            ]
        ),
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.pick.player_id == 2
    assert len(calls) == 2


def test_a_roll_decision_carries_no_pick_and_no_alternative(monkeypatch):
    """A "do nothing this week" answer must not name a player. It used to have
    to: pick_player_id was a required key, so a correct roll still arrived
    carrying a filler player, which the dashboard then displayed in large type
    as though it were the recommendation."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "roll",
                        "confidence": 0.7,
                        "evidence": ["Squad is fully fit; no forced move."],
                        "risks": ["Banking a transfer costs flexibility."],
                    },
                    "call_1",
                )
            ]
        )
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    result = reasoning.reason("Should I use my transfer?", [_team_tool()])

    assert result.decision == "roll"
    assert result.pick is None
    assert result.alternative is None


def test_an_alternative_equal_to_the_pick_is_dropped_not_substituted(monkeypatch):
    """A duplicate alternative id used to be silently swapped for "any other
    known player", putting a player the model never argued for in front of the
    user as the ALT."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "captain",
                        "pick_player_id": 1,
                        "confidence": 0.6,
                        "evidence": ["e"],
                        "risks": ["r"],
                        "alternative_player_id": 1,  # same player as the pick
                        "alternative_reason": "x",
                    },
                    "call_1",
                )
            ]
        )
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.pick.player_id == 1
    assert result.alternative is None


def test_an_illegal_transfer_is_bounced_back_rather_than_returned(monkeypatch):
    """Issue 22: the legality gate on the finalisation path.

    ADR-0004 already says the pick is built from an authoritative lookup
    rather than from model text. "and it must be a legal move" is the same
    principle carried one step further -- so a refused submit goes back
    through the same is_error channel as a malformed one, and the model gets
    another turn instead of the user getting an impossible move.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "transfer",
                        "pick_player_id": 2,  # Player B, the illegal target
                        "confidence": 0.9,
                        "evidence": ["his numbers are better"],
                        "risks": ["r"],
                    },
                    "call_1",
                )
            ]
        ),
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "roll",
                        "confidence": 0.6,
                        "evidence": ["no legal move is worth making"],
                        "risks": ["r"],
                    },
                    "call_2",
                )
            ]
        ),
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    def validate(result):
        if result.decision == "transfer" and result.pick is not None:
            return [f"{result.pick.player_name} is a FWD and you hold no FWD to sell."]
        return []

    result = reasoning.reason("Should I buy Player B?", [_team_tool()], validate=validate)

    # The refused submit did not become the answer.
    assert result.decision == "roll"
    assert result.pick is None
    # And the model was told why, through the error channel, so it could revise.
    assert len(calls) == 2
    bounced = calls[1]["messages"][-1]["content"][0]
    assert bounced["is_error"] is True
    assert "not legal in FPL" in bounced["content"]
    assert "no FWD to sell" in bounced["content"]


def test_a_legal_transfer_passes_the_gate_untouched(monkeypatch):
    """The gate must not make itself felt on a legal move: a validator that
    returns nothing changes neither the answer nor the number of turns."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    responses = [
        _response(
            [
                _tool_use(
                    "submit_recommendation",
                    {
                        "decision": "transfer",
                        "pick_player_id": 1,
                        "confidence": 0.8,
                        "evidence": ["e"],
                        "risks": ["r"],
                    },
                    "call_1",
                )
            ]
        )
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    result = reasoning.reason(
        "Should I buy Player A?", [_team_tool()], validate=lambda _: []
    )

    assert result.decision == "transfer"
    assert result.pick.player_id == 1
    assert len(calls) == 1


def test_a_plan_step_naming_an_illegal_transfer_is_bounced_back(monkeypatch):
    """A plan's week-3 step being impossible is not less wrong for being
    three weeks away."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    illegal_plan = {
        "horizon": "multi-week",
        "confidence": 0.7,
        "steps": [
            {
                "gameweek": 10,
                "decision": "roll",
                "evidence": ["e"],
                "risks": ["r"],
            },
            {
                "gameweek": 11,
                "decision": "transfer",
                "pick_player_id": 2,
                "evidence": ["e"],
                "risks": ["r"],
            },
        ],
    }
    legal_plan = {
        "horizon": "multi-week",
        "confidence": 0.7,
        "steps": [
            {"gameweek": 10, "decision": "roll", "evidence": ["e"], "risks": ["r"]},
            {"gameweek": 11, "decision": "roll", "evidence": ["e"], "risks": ["r"]},
        ],
    }
    responses = [
        _response([_tool_use("submit_plan", illegal_plan, "call_1")]),
        _response([_tool_use("submit_plan", legal_plan, "call_2")]),
    ]
    calls = []

    def fake_create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create

    def validate(result):
        steps = getattr(result, "steps", [])
        return [
            "Player B cannot be bought."
            for step in steps
            if step.decision == "transfer" and step.pick is not None
        ]

    result = reasoning.reason("Plan my next few weeks", [_team_tool()], validate=validate)

    assert [step.decision for step in result.steps] == ["roll", "roll"]
    assert len(calls) == 2


def test_a_plan_step_carries_both_sides_of_the_transfer(monkeypatch):
    """Issue 25: `out_player_id` reaches PlanStep.player_out.

    Without it a plan cannot be checked at all -- the budget after step 2 is
    unknowable, so step 3's affordability is unknowable too, which is exactly
    why ticket 12's plans went unvalidated. Both ids resolve through
    known_players, so neither side of a transfer can be a player the model
    invented (ADR-0004).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    plan = {
        "horizon": "multi-week",
        "confidence": 0.7,
        "steps": [
            {
                "gameweek": 10,
                "decision": "transfer",
                "pick_player_id": 2,
                "out_player_id": 1,
                "evidence": ["e"],
                "risks": ["r"],
            },
            {"gameweek": 11, "decision": "roll", "evidence": ["e"], "risks": ["r"]},
        ],
    }
    reasoning._client.messages.create = lambda **kwargs: _response(
        [_tool_use("submit_plan", plan, "call_1")]
    )

    result = reasoning.reason("Plan my next few weeks", [_team_tool()], validate=lambda _: [])

    transfer, roll = result.steps
    assert transfer.pick.player_name == "Player B"
    assert transfer.player_out.player_name == "Player A"
    # A 'roll' week names nobody on either side.
    assert roll.pick is None and roll.player_out is None


def test_an_unrecorded_out_player_id_is_dropped_rather_than_guessed(monkeypatch):
    """Same discipline as the pick: an id the model invented resolves to
    nothing, and the plan validator then refuses the step for naming no
    outgoing player -- which is the honest failure. Substituting some other
    known player would silently sell somebody nobody argued for."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    reasoning = ClaudeReasoning()

    plan = {
        "horizon": "multi-week",
        "confidence": 0.7,
        "steps": [
            {
                "gameweek": 10,
                "decision": "transfer",
                "pick_player_id": 2,
                "out_player_id": 999,  # never returned by any tool
                "evidence": ["e"],
                "risks": ["r"],
            },
            {"gameweek": 11, "decision": "roll", "evidence": ["e"], "risks": ["r"]},
        ],
    }
    reasoning._client.messages.create = lambda **kwargs: _response(
        [_tool_use("submit_plan", plan, "call_1")]
    )

    result = reasoning.reason("Plan my next few weeks", [_team_tool()], validate=lambda _: [])

    assert result.steps[0].player_out is None
