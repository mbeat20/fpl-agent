import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.get_player import get_player, make_get_player_tool

TEST_IDS = (901, 902)
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
                        901,
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
                        902,
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


def test_get_player_matches_by_exact_full_name(pool_players):
    with _conn() as conn:
        player = get_player(conn, "Zeb Zarkwing")

    assert player.id == 901
    assert player.xg == 7.2
    assert player.xa == 6.1


def test_get_player_matches_case_insensitively(pool_players):
    with _conn() as conn:
        player = get_player(conn, "zolstrike")

    assert player.id == 902


def test_get_player_matches_a_surname_on_its_own(pool_players):
    """"Partial" now means a whole name the player is known by -- a surname
    without the forename -- not any substring of one."""
    with _conn() as conn:
        player = get_player(conn, "Zarkwing")

    assert player.id == 901


def test_get_player_rejects_a_truncated_word(pool_players):
    """`Zarkw` used to resolve to Zarkwing through
    `ILIKE %name% ORDER BY name LIMIT 1`. That fallback is gone: on the live
    corpus it was turning auto-transcript fragments into confident
    attributions -- "Jac" became Butland, "Rya" became Christie, "Fred"
    became Fredricson -- and answering an ambiguous name by sort order split
    ten "Palmer" stances between Cole and Alex.

    Raising sends a model that guessed back for a fuller name, which is the
    outcome worth having under ADR-0004, where the pick is a lookup by
    recorded id."""
    with _conn() as conn:
        with pytest.raises(ValueError):
            get_player(conn, "Zarkw")


def test_get_player_raises_when_no_match(pool_players):
    with _conn() as conn:
        with pytest.raises(ValueError):
            get_player(conn, "Definitely Not A Real Player Name")


def test_make_get_player_tool_handler_resolves_a_player(pool_players):
    with _conn() as conn:
        tool = make_get_player_tool(conn)
        player = tool.handler(name="Zarkwing")

    assert player.id == 901
    assert player.name == "Zarkwing"
