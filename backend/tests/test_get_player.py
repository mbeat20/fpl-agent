import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.get_player import get_player, make_get_player_tool

TEST_IDS = (901, 902)


@pytest.fixture
def pool_players():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))
            cur.executemany(
                "INSERT INTO fpl_players "
                "(id, name, full_name, position, club, price, ownership, form, status, "
                " news, xg, xa) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        901,
                        "Saka",
                        "Bukayo Saka",
                        "MID",
                        "Arsenal",
                        10.0,
                        30.0,
                        5.1,
                        "a",
                        None,
                        7.2,
                        6.1,
                    ),
                    (
                        902,
                        "Haaland",
                        "Erling Haaland",
                        "FWD",
                        "Man City",
                        15.0,
                        60.1,
                        8.2,
                        "a",
                        None,
                        28.8,
                        5.5,
                    ),
                ],
            )
    yield TEST_IDS
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def test_get_player_matches_by_exact_full_name(pool_players):
    with _conn() as conn:
        player = get_player(conn, "Bukayo Saka")

    assert player.id == 901
    assert player.xg == 7.2
    assert player.xa == 6.1


def test_get_player_matches_case_insensitively(pool_players):
    with _conn() as conn:
        player = get_player(conn, "haaland")

    assert player.id == 902


def test_get_player_falls_back_to_a_partial_name_match(pool_players):
    with _conn() as conn:
        player = get_player(conn, "Buka")

    assert player.id == 901


def test_get_player_raises_when_no_match(pool_players):
    with _conn() as conn:
        with pytest.raises(ValueError):
            get_player(conn, "Definitely Not A Real Player Name")


def test_make_get_player_tool_handler_resolves_a_player(pool_players):
    with _conn() as conn:
        tool = make_get_player_tool(conn)
        player = tool.handler(name="Saka")

    assert player.id == 901
    assert player.name == "Saka"
