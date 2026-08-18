import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from fpl_coach.db import get_connection
from fpl_coach.instincts import is_instinct, log_instinct
from fpl_coach.models import Recommendation
from fpl_coach.preferences import (
    append_preference,
    build_system_prompt,
    extract_remember_instruction,
    load_preferences,
)
from fpl_coach.reasoning import ClaudeReasoning, FakeReasoning, Reasoning
from fpl_coach.tools import (
    make_compare_players_tool,
    make_get_creator_opinions_tool,
    make_get_injury_news_tool,
    make_get_instincts_tool,
    make_get_my_team_tool,
    make_get_player_tool,
    make_predict_price_changes_tool,
)

router = APIRouter()

MY_TEAM_ID = 1  # v1 MVP scope (initial-plan.md): single user, one team.


class AskRequest(BaseModel):
    question: str


class RememberAck(BaseModel):
    remembered: str


def get_reasoning() -> Reasoning:
    """Overridden in tests via app.dependency_overrides (Seam-1: FakeReasoning)."""
    if os.environ.get("REASONING_IMPL", "claude") == "fake":
        return FakeReasoning()
    return ClaudeReasoning()


@router.post("/recommendations", response_model=Recommendation | RememberAck)
def ask(
    request: AskRequest, reasoning: Reasoning = Depends(get_reasoning)
) -> Recommendation | RememberAck:
    remember_text = extract_remember_instruction(request.question)
    if remember_text is not None:
        if is_instinct(remember_text):
            with get_connection() as conn:
                log_instinct(conn, MY_TEAM_ID, remember_text)
        else:
            append_preference(remember_text)
        return RememberAck(remembered=remember_text)

    with get_connection() as conn:
        tools = [
            make_get_my_team_tool(conn, MY_TEAM_ID),
            make_get_player_tool(conn),
            make_compare_players_tool(conn),
            make_get_injury_news_tool(conn, MY_TEAM_ID),
            make_get_creator_opinions_tool(conn),
            make_predict_price_changes_tool(conn),
            make_get_instincts_tool(conn, MY_TEAM_ID),
        ]
        system = build_system_prompt(load_preferences())
        return reasoning.reason(request.question, tools, system=system)
