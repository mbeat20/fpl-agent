import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.models import (
    Alternative,
    Evidence,
    PlanStep,
    Recommendation,
    RecommendationPick,
    TransferPlan,
)
from fpl_coach.suggestions import get_cached_suggestion, save_suggestion

TEST_TEAM_ID = 9115


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def team():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Test Team"))
    yield TEST_TEAM_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))  # cascades


def _recommendation() -> Recommendation:
    return Recommendation(
        decision="captain",
        pick=RecommendationPick(player_id=1, player_name="Salah"),
        confidence=0.8,
        evidence=[Evidence(text="good fixture")],
        risks=["rotation"],
        alternative=Alternative(player_id=2, player_name="Haaland", reason="also good"),
    )


def _plan() -> TransferPlan:
    return TransferPlan(
        horizon="multi-week",
        confidence=0.6,
        steps=[
            PlanStep(
                gameweek=1,
                decision="transfer",
                pick=RecommendationPick(player_id=1, player_name="Salah"),
                evidence=[Evidence(text="e")],
                risks=["r"],
            ),
            PlanStep(
                gameweek=2,
                decision="transfer",
                pick=RecommendationPick(player_id=2, player_name="Haaland"),
                evidence=[Evidence(text="e2")],
                risks=["r2"],
            ),
        ],
    )


def test_get_cached_suggestion_is_none_when_nothing_saved(team):
    with _conn() as conn:
        result = get_cached_suggestion(conn, team, gameweek=3)

    assert result is None


def test_save_and_get_round_trips_a_recommendation(team):
    with _conn() as conn:
        save_suggestion(conn, team, gameweek=3, result=_recommendation())
        result = get_cached_suggestion(conn, team, gameweek=3)

    assert isinstance(result, Recommendation)
    assert result == _recommendation()


def test_save_and_get_round_trips_a_transfer_plan(team):
    with _conn() as conn:
        save_suggestion(conn, team, gameweek=3, result=_plan())
        result = get_cached_suggestion(conn, team, gameweek=3)

    assert isinstance(result, TransferPlan)
    assert result == _plan()


def test_a_suggestion_is_invisible_once_the_gameweek_moves_on(team):
    """Same "stale row never resurfaces" behaviour issue 13's get_instincts
    relies on for its own gameweek scoping -- a gameweek-3 suggestion must
    not be returned for a gameweek-4 lookup."""
    with _conn() as conn:
        save_suggestion(conn, team, gameweek=3, result=_recommendation())
        result = get_cached_suggestion(conn, team, gameweek=4)

    assert result is None


def test_saving_again_overwrites_rather_than_accumulates(team):
    with _conn() as conn:
        save_suggestion(conn, team, gameweek=3, result=_recommendation())
        save_suggestion(conn, team, gameweek=4, result=_plan())

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM team_suggestions WHERE team_id = %s", (team,))
            row = cur.fetchone()
        result = get_cached_suggestion(conn, team, gameweek=4)

    assert row["n"] == 1
    assert isinstance(result, TransferPlan)
