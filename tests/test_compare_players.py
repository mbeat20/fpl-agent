import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.compare_players import compare_players, make_compare_players_tool

TEST_IDS = (911, 912)


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
                        911,
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
                        912,
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


def test_compare_players_returns_both_matches(pool_players):
    with _conn() as conn:
        comparison = compare_players(conn, ["Saka", "Haaland"])

    assert {p.id for p in comparison.players} == {911, 912}


def test_compare_players_silently_skips_unmatched_names(pool_players):
    with _conn() as conn:
        comparison = compare_players(conn, ["Saka", "No Such Player"])

    assert [p.name for p in comparison.players] == ["Saka"]


def test_compare_players_raises_when_nothing_matches(pool_players):
    with _conn() as conn:
        with pytest.raises(ValueError):
            compare_players(conn, ["Nobody One", "Nobody Two"])


def test_make_compare_players_tool_handler_resolves_players(pool_players):
    with _conn() as conn:
        tool = make_compare_players_tool(conn)
        comparison = tool.handler(names=["Saka", "Haaland"])

    assert {p.id for p in comparison.players} == {911, 912}
