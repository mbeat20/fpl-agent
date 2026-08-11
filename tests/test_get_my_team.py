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
