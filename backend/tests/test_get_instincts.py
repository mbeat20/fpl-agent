import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.get_instincts import get_instincts, make_get_instincts_tool

TEST_TEAM_ID = 9103


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def team_at_gameweek_5():
    """One current-gameweek Instinct and one stale, leftover-from-gameweek-4
    Instinct -- issue 13 AC4: a new gameweek must not surface old Instincts."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Test Team"))
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)", (TEST_TEAM_ID, 5)
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
                (TEST_TEAM_ID, 5, "thinking Rogers to Enzo"),
            )
            cur.execute(
                "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
                (TEST_TEAM_ID, 4, "thinking Salah captain"),
            )
    yield TEST_TEAM_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))  # cascades


def test_get_instincts_is_empty_before_a_gameweek_is_known():
    with _conn() as conn:
        result = get_instincts(conn, 999999)

    assert result.notes == []


def test_get_instincts_only_surfaces_the_current_gameweeks_notes(team_at_gameweek_5):
    with _conn() as conn:
        result = get_instincts(conn, team_at_gameweek_5)

    assert [n.text for n in result.notes] == ["thinking Rogers to Enzo"]


def test_make_get_instincts_tool_handler_returns_the_notes(team_at_gameweek_5):
    with _conn() as conn:
        tool = make_get_instincts_tool(conn, team_at_gameweek_5)
        result = tool.handler()

    assert len(result.notes) == 1
    assert result.notes[0].text == "thinking Rogers to Enzo"
