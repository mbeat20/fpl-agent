import os

import psycopg
from psycopg.rows import dict_row

from fpl_coach.tools import get_my_team


def test_get_my_team_reads_seeded_data(seeded_team):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        team = get_my_team(conn, seeded_team)

    assert team.name == "Test Team"
    assert {player.name for player in team.players} == {
        "Mohamed Salah",
        "Erling Haaland",
        "Bukayo Saka",
    }


def test_get_my_team_current_gameweek_is_null_until_ingestion_has_run(seeded_team):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        team = get_my_team(conn, seeded_team)

    assert team.current_gameweek is None


def test_get_my_team_exposes_the_current_gameweek(seeded_team):
    """Issue 12: a Reasoning implementation anchors a multi-week plan's step
    gameweeks to this."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 9),
        )

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        team = get_my_team(conn, seeded_team)

    assert team.current_gameweek == 9
