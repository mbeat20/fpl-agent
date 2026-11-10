import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.compare_players import compare_players, make_compare_players_tool

TEST_IDS = (911, 912)
# Issue 22 / 2026-08-30: these were "Saka" and "Haaland" with their real full
# names, and they passed only while `fpl_players` happened to be empty of real
# data. Once `fpl-ingest` has run, `find_player`'s exact-match query --
# `WHERE lower(name) = lower(%s) ... LIMIT 1`, with no ORDER BY -- can return
# the REAL Saka (id 12) instead of the seeded one, and the assertion on the
# fake id fails.
#
# This is the "false green" failure mode STATUS.md records: a test that is
# green only because the shared database is empty. The fix is names no real
# Premier League player can collide with, on `name`, on `full_name`, and on
# the substring fallback -- the same discipline test_recommend_transfers and
# the ticket 21/22 fixtures already use.



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
                        "Zarkwing",
                        "Zeb Zarkwing",
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
                        "Zolstrike",
                        "Zorin Zolstrike",
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
        comparison = compare_players(conn, ["Zarkwing", "Zolstrike"])

    assert {p.id for p in comparison.players} == {911, 912}


def test_compare_players_silently_skips_unmatched_names(pool_players):
    with _conn() as conn:
        comparison = compare_players(conn, ["Zarkwing", "No Such Player"])

    assert [p.name for p in comparison.players] == ["Zarkwing"]


def test_compare_players_raises_when_nothing_matches(pool_players):
    with _conn() as conn:
        with pytest.raises(ValueError):
            compare_players(conn, ["Nobody One", "Nobody Two"])


def test_make_compare_players_tool_handler_resolves_players(pool_players):
    with _conn() as conn:
        tool = make_compare_players_tool(conn)
        comparison = tool.handler(names=["Zarkwing", "Zolstrike"])

    assert {p.id for p in comparison.players} == {911, 912}
