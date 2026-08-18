import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.recommend_transfers import make_recommend_transfers_tool, recommend_transfers

TEST_IDS = (961, 962)
TEST_CLUBS = ("RTOUT", "RTIN")  # fake, unambiguous club codes -- no real-fixture collision risk
TEST_FIXTURE_ID = 9201
TEST_HANDLE = "recommend_transfers_test_creator"

FUTURE = datetime.now(timezone.utc) + timedelta(days=7)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def two_players():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))
            cur.execute("DELETE FROM fixtures WHERE id = %s", (TEST_FIXTURE_ID,))
            cur.executemany(
                "INSERT INTO fpl_players "
                "(id, name, full_name, position, club, price, ownership, form, status, "
                " news, xg, xa) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        TEST_IDS[0], "Out Player", "Out Player Full", "MID", TEST_CLUBS[0],
                        6.0, 10.0, 3.0, "a", None, 2.0, 1.0,
                    ),
                    (
                        TEST_IDS[1], "In Player", "In Player Full", "FWD", TEST_CLUBS[1],
                        8.0, 20.0, 4.0, "a", None, 5.0, 2.0,
                    ),
                ],
            )
            cur.execute(
                "INSERT INTO fixtures "
                "(id, gameweek, home_team, away_team, home_difficulty, away_difficulty, "
                " kickoff_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (TEST_FIXTURE_ID, 10, TEST_CLUBS[0], TEST_CLUBS[1], 3, 2, FUTURE),
            )
        conn.commit()
    yield TEST_IDS
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))
        conn.execute("DELETE FROM fixtures WHERE id = %s", (TEST_FIXTURE_ID,))


def test_gain_signal_and_net_gain_after_a_hit(two_players):
    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=True)

    # out signal = 2.0 + 1.0 + 3.0 = 6.0; in signal = 5.0 + 2.0 + 4.0 = 11.0
    assert comparison.gain_signal == pytest.approx(5.0)
    assert comparison.hit_cost == 4
    assert comparison.net_gain_signal == pytest.approx(1.0)
    assert comparison.data_complete is True


def test_hit_cost_is_zero_when_not_a_hit(two_players):
    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=False)

    assert comparison.hit_cost == 0
    assert comparison.net_gain_signal == comparison.gain_signal


def test_data_complete_is_false_when_a_player_is_missing_stats(two_players):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE fpl_players SET xg = NULL WHERE id = %s", (TEST_IDS[1],))

    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=False)

    assert comparison.data_complete is False


def test_injury_risk_is_flagged_per_player(two_players):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("UPDATE fpl_players SET status = 'i' WHERE id = %s", (TEST_IDS[0],))

    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=False)

    assert comparison.player_out.injury_risk is True
    assert comparison.player_in.injury_risk is False


def test_next_fixture_is_resolved_per_player(two_players):
    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=False)

    assert comparison.player_out.next_opponent == TEST_CLUBS[1]
    assert comparison.player_out.next_fixture_difficulty == 3
    assert comparison.player_in.next_opponent == TEST_CLUBS[0]
    assert comparison.player_in.next_fixture_difficulty == 2


@pytest.fixture
def creator_stance_for_out_player(two_players):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Recommend Transfers Test Creator", TEST_HANDLE, "high"),
            )
            creator_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (creator_id, f"https://x.com/{TEST_HANDLE}/status/1", "Sell Out Player."),
            )
            content_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (creator_id, content_id, "Out Player", "sell", 0.7, "Bad fixtures."),
            )
        conn.commit()
    yield two_players
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))


def test_creator_opinions_are_included_per_player(creator_stance_for_out_player):
    with _conn() as conn:
        comparison = recommend_transfers(conn, "Out Player", "In Player", is_hit=False)

    assert len(comparison.player_out.creator_stances) == 1
    assert comparison.player_out.creator_stances[0].stance == "sell"
    assert comparison.player_in.creator_stances == []


def test_make_recommend_transfers_tool_handler_is_wired(two_players):
    with _conn() as conn:
        tool = make_recommend_transfers_tool(conn)
        comparison = tool.handler(player_out_name="Out Player", player_in_name="In Player", is_hit=True)

    assert comparison.player_out.player.name == "Out Player"
    assert comparison.player_in.player.name == "In Player"
    assert comparison.is_hit is True
