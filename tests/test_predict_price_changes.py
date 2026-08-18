import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.predict_price_changes import make_predict_price_changes_tool, predict_price_changes

TEST_IDS = (971, 972, 973, 974)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def pool_with_price_movers():
    """Four pool players covering the tool's decision surface: a
    high-ownership riser, a low-ownership riser whose raw transfer count is
    smaller but should still rank first (issue 08's ownership-relative
    scoring), a faller, and a player with no net movement (excluded)."""
    with _conn() as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fpl_players "
                "(id, name, full_name, position, club, price, ownership, "
                " transfers_in_event, transfers_out_event, cost_change_event) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (971, "BigOwned", "Big Owned Riser", "MID", "Arsenal", 10.0, 50.0, 20000, 2000, 0),
                    (972, "SmallOwned", "Small Owned Riser", "MID", "Brentford", 4.5, 2.0, 3000, 100, 0),
                    (973, "Faller", "Falling Player", "DEF", "Everton", 5.0, 20.0, 200, 9000, -1),
                    (974, "NoMove", "No Movement", "FWD", "Fulham", 6.0, 10.0, 500, 500, 0),
                ],
            )
        conn.commit()
    yield TEST_IDS
    with _conn() as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_IDS),))


def test_players_with_no_net_transfers_are_excluded(pool_with_price_movers):
    with _conn() as conn:
        predictions = predict_price_changes(conn)

    all_ids = {p.player_id for p in predictions.rising + predictions.falling}
    assert 974 not in all_ids


def test_low_ownership_riser_ranks_above_high_ownership_riser(pool_with_price_movers):
    """SmallOwned has far fewer raw net transfers (2900) than BigOwned
    (18000), but needs far fewer transfers to move price at 2% ownership --
    the ranking must reflect that, not raw transfer counts."""
    with _conn() as conn:
        predictions = predict_price_changes(conn)

    rising_ids = [p.player_id for p in predictions.rising]
    assert rising_ids.index(972) < rising_ids.index(971)


def test_faller_is_flagged_in_the_falling_list(pool_with_price_movers):
    with _conn() as conn:
        predictions = predict_price_changes(conn)

    falling_ids = {p.player_id for p in predictions.falling}
    assert 973 in falling_ids
    assert 973 not in {p.player_id for p in predictions.rising}


def test_evidence_notes_when_price_already_moved_today(pool_with_price_movers):
    with _conn() as conn:
        predictions = predict_price_changes(conn)

    faller = next(p for p in predictions.falling if p.player_id == 973)
    assert "already moved" in faller.evidence
    riser = next(p for p in predictions.rising if p.player_id == 971)
    assert "already moved" not in riser.evidence


def test_limit_caps_each_direction_independently(pool_with_price_movers):
    with _conn() as conn:
        predictions = predict_price_changes(conn, limit=1)

    assert len(predictions.rising) == 1
    assert len(predictions.falling) == 1
    assert predictions.rising[0].player_id == 972


def test_make_predict_price_changes_tool_handler_returns_predictions(pool_with_price_movers):
    with _conn() as conn:
        tool = make_predict_price_changes_tool(conn)
        predictions = tool.handler()

    assert any(p.player_id == 972 for p in predictions.rising)
