import os

from fastapi import APIRouter, Depends
from psycopg import Connection
from pydantic import BaseModel

from fpl_coach.db import get_connection
from fpl_coach.ingestion.voyage_client import VoyageClient
from fpl_coach.instincts import current_gameweek, is_instinct, log_instinct
from fpl_coach.models import Instincts, Recommendation, Team, TransferPlan
from fpl_coach.outcomes import log_recommendation
from fpl_coach.preferences import (
    append_preference,
    build_system_prompt,
    extract_remember_instruction,
    load_preferences,
)
from fpl_coach.reasoning import ClaudeReasoning, FakeReasoning, Reasoning
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.suggestions import get_cached_suggestion, save_suggestion
from fpl_coach.tools import (
    make_compare_players_tool,
    make_get_creator_opinions_tool,
    make_get_injury_news_tool,
    make_get_instincts_tool,
    make_get_my_team_tool,
    make_get_player_tool,
    make_predict_price_changes_tool,
    make_recommend_transfers_tool,
    make_search_creator_opinions_tool,
)
from fpl_coach.tools.get_instincts import get_instincts
from fpl_coach.tools.get_my_team import get_my_team

router = APIRouter()

MY_TEAM_ID = 1  # v1 MVP scope (initial-plan.md): single user, one team.

# Issue 15: the Team dashboard's unprompted suggestion -- deliberately framed
# as a single-gameweek ask (not "plan my season") so Claude's own
# submit_recommendation-vs-submit_plan judgment (claude.py) is nudged toward
# a Recommendation, matching what the dashboard is meant to show. Nothing
# forces this -- a TransferPlan is still a valid response and the route
# handles either, same as /recommendations.
_SUGGESTION_PROMPT = (
    "Based on everything currently known about my squad, fixtures, injuries, and trusted "
    "creator opinions, is there anything I should know or consider for this gameweek? Give "
    "me your single most useful captain, transfer, or hold recommendation right now, without "
    "me having to ask."
)


class AskRequest(BaseModel):
    question: str


class RememberAck(BaseModel):
    remembered: str


def get_reasoning() -> Reasoning:
    """Overridden in tests via app.dependency_overrides (Seam-1: FakeReasoning)."""
    if os.environ.get("REASONING_IMPL", "claude") == "fake":
        return FakeReasoning()
    return ClaudeReasoning()


def _build_tools(conn: Connection) -> list[ToolSpec]:
    return [
        make_get_my_team_tool(conn, MY_TEAM_ID),
        make_get_player_tool(conn),
        make_compare_players_tool(conn),
        make_get_injury_news_tool(conn, MY_TEAM_ID),
        make_get_creator_opinions_tool(conn),
        make_predict_price_changes_tool(conn),
        make_get_instincts_tool(conn, MY_TEAM_ID),
        make_search_creator_opinions_tool(conn, VoyageClient()),
        make_recommend_transfers_tool(conn),
    ]


def _log_result(conn: Connection, result: Recommendation | TransferPlan) -> None:
    if isinstance(result, TransferPlan):
        # Issue 12: only the nearest step is an actual commitment right
        # now -- record_pending_outcomes (ADR-0012) only ever resolves
        # the immediately-prior gameweek's rows, so logging every
        # future-dated step here would leave the rest as permanently
        # unresolved speculation, not real history. `min` rather than
        # `steps[0]`: nothing in the submit_plan schema or its
        # description requires Claude to emit steps in gameweek order.
        nearest = min(result.steps, key=lambda step: step.gameweek)
        log_recommendation(
            conn,
            MY_TEAM_ID,
            nearest.pick.player_id,
            nearest.pick.player_name,
            gameweek=nearest.gameweek,
        )
    else:
        log_recommendation(conn, MY_TEAM_ID, result.pick.player_id, result.pick.player_name)


@router.post("/recommendations", response_model=Recommendation | TransferPlan | RememberAck)
def ask(
    request: AskRequest, reasoning: Reasoning = Depends(get_reasoning)
) -> Recommendation | TransferPlan | RememberAck:
    remember_text = extract_remember_instruction(request.question)
    if remember_text is not None:
        if is_instinct(remember_text):
            with get_connection() as conn:
                log_instinct(conn, MY_TEAM_ID, remember_text)
        else:
            append_preference(remember_text)
        return RememberAck(remembered=remember_text)

    with get_connection() as conn:
        tools = _build_tools(conn)
        system = build_system_prompt(load_preferences())
        result = reasoning.reason(request.question, tools, system=system)
        _log_result(conn, result)
        return result


@router.get("/team", response_model=Team)
def team() -> Team:
    with get_connection() as conn:
        return get_my_team(conn, MY_TEAM_ID)


@router.get("/team/instincts", response_model=Instincts)
def team_instincts() -> Instincts:
    with get_connection() as conn:
        return get_instincts(conn, MY_TEAM_ID)


@router.get("/team/suggestion", response_model=Recommendation | TransferPlan | None)
def team_suggestion(
    reasoning: Reasoning = Depends(get_reasoning),
) -> Recommendation | TransferPlan | None:
    with get_connection() as conn:
        gameweek = current_gameweek(conn, MY_TEAM_ID)
        if gameweek is None:
            # No ingestion has run yet -- nothing to base a suggestion on.
            return None

        cached = get_cached_suggestion(conn, MY_TEAM_ID, gameweek)
        if cached is not None:
            return cached

        tools = _build_tools(conn)
        system = build_system_prompt(load_preferences())
        result = reasoning.reason(_SUGGESTION_PROMPT, tools, system=system)
        save_suggestion(conn, MY_TEAM_ID, gameweek, result)
        _log_result(conn, result)
        return result
