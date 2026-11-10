import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.fixtures import upcoming_gameweek
from fpl_coach.instincts import current_gameweek, is_instinct, log_instinct

TEST_TEAM_ID = 9002


@pytest.fixture
def team_with_gameweek():
    """A team row plus its resolved gameweek (issue 13's gameweek_state),
    isolated from MY_TEAM_ID so this never touches the real app's state."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Test Team"))
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)", (TEST_TEAM_ID, 3)
        )
    yield TEST_TEAM_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))  # cascades gameweek_state/instincts


def test_is_instinct_matches_the_thinking_marker():
    assert is_instinct("thinking Rogers to Enzo")
    assert is_instinct("  Thinking   Haaland captain")


def test_is_instinct_rejects_standing_preference_text():
    assert not is_instinct("I dislike knee-jerk transfers")
    assert not is_instinct("Takes captain risks when chasing rank")


def test_current_gameweek_is_none_before_ingestion_has_run():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        assert current_gameweek(conn, TEST_TEAM_ID) is None


def test_log_instinct_falls_back_to_the_fixture_derived_gameweek():
    """Issue 18: log_instinct moved from current_gameweek (gameweek_state
    alone) to planning_gameweek, which also reads fixtures. A team that has
    never been ingested therefore no longer hard-fails whenever the calendar
    can still say which deadline is next -- refusing to record the user's own
    note over a missing ingestion row helped nobody. It still raises when
    NEITHER source knows.

    Both branches are asserted against what planning_gameweek actually
    resolves to, because `fixtures` is global and run_ingestion replaces it
    wholesale -- so whether the fallback has anything to work with depends on
    what else has run in this session. Pinning it to one branch made it pass
    or fail on test ORDER."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute(
            "INSERT INTO teams (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (TEST_TEAM_ID, "Instinct Fallback Team"),
        )
        conn.commit()
        expected = upcoming_gameweek(conn)
        try:
            if expected is None:
                with pytest.raises(ValueError, match="gameweek"):
                    log_instinct(conn, TEST_TEAM_ID, "thinking Rogers to Enzo")
            else:
                log_instinct(conn, TEST_TEAM_ID, "thinking Rogers to Enzo")
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT gameweek FROM instincts WHERE team_id = %s", (TEST_TEAM_ID,)
                    )
                    row = cur.fetchone()
                assert row is not None
                assert row["gameweek"] == expected
        finally:
            conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
            conn.commit()


def test_log_instinct_writes_a_row_tagged_to_the_current_gameweek(team_with_gameweek):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        log_instinct(conn, team_with_gameweek, "thinking Rogers to Enzo")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT text, gameweek FROM instincts WHERE team_id = %s", (team_with_gameweek,)
            )
            row = cur.fetchone()

    assert row["text"] == "thinking Rogers to Enzo"
    assert row["gameweek"] == 3
