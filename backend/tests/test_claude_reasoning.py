"""Unit tests for ClaudeReasoning's own control flow, with the Anthropic
client mocked out -- no network calls, no API key spend. Everything else
about ClaudeReasoning (does Claude actually infer the right Horizon, cite
real evidence, etc.) requires the real model and lives in
test_recommendations_integration.py instead."""

from types import SimpleNamespace

from fpl_coach.models import Player, Team
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.reasoning.claude import ClaudeReasoning


def _tool_use(name, input_, id_):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def _response(blocks):
    return SimpleNamespace(content=blocks, stop_reason="tool_use")


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
                        "pick_player_id": 1,
                        "confidence": 0.5,
                        "evidence": ["e"],
                        "risks": ["r"],
                        # alternative_player_id missing entirely
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
