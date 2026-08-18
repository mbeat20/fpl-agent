import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.get_creator_opinions import get_creator_opinions, make_get_creator_opinions_tool

TEST_HANDLES = ("opinions_high", "opinions_low")


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = ANY(%s)", (list(TEST_HANDLES),))


@pytest.fixture
def seeded_opinions():
    """Hand-seeds two creators (high/low trust) each with one Stance on the
    same player, with zero external APIs -- covers criteria 1 + 5 of issue
    06 independent of the Grok/Voyage-backed criteria."""
    _cleanup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("High Trust Creator", "opinions_high", "high"),
            )
            high_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Low Trust Creator", "opinions_low", "low"),
            )
            low_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (high_id, "https://x.com/opinions_high/status/1", "Buy Watkins, he's undervalued."),
            )
            high_content_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (low_id, "https://x.com/opinions_low/status/1", "Sell Watkins, rotation risk."),
            )
            low_content_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (high_id, high_content_id, "Ollie Watkins", "buy", 0.8, "Undervalued given fixtures."),
            )
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (low_id, low_content_id, "Ollie Watkins", "sell", 0.4, "Rotation risk."),
            )
        conn.commit()
    yield
    _cleanup()


def test_returns_stances_from_every_creator_who_has_one(seeded_opinions):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Ollie Watkins")

    creator_names = {s.creator_name for s in opinions.stances}
    assert creator_names == {"High Trust Creator", "Low Trust Creator"}


def test_stances_are_sorted_with_higher_trust_tier_first(seeded_opinions):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Ollie Watkins")

    assert [s.trust_tier for s in opinions.stances] == ["high", "low"]
    assert opinions.stances[0].creator_name == "High Trust Creator"
    assert opinions.stances[0].stance == "buy"


def test_partial_name_match_finds_the_player(seeded_opinions):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Watkins")

    assert len(opinions.stances) == 2


def test_a_player_with_no_stances_returns_an_empty_list(seeded_opinions):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Some Player Nobody Has Discussed")

    assert opinions.stances == []


def test_make_get_creator_opinions_tool_handler_returns_the_view(seeded_opinions):
    with _conn() as conn:
        tool = make_get_creator_opinions_tool(conn)
        opinions = tool.handler(player_name="Watkins")

    assert len(opinions.stances) == 2
