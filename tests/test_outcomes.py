import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.outcomes import log_recommendation, record_pending_outcomes

TEST_HANDLE = "outcomes_test_creator"

FAKE_ELEMENT = {"cost_change_event": 2, "event_points": 8}  # +0.2 price change, 8 points


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def test_log_recommendation_writes_a_row_tagged_with_the_current_gameweek(seeded_team):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 4),
        )
        log_recommendation(conn, seeded_team, 1093, "Mohamed Salah")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek, player_id, player_name, outcome_recorded_at "
                "FROM recommendation_log WHERE team_id = %s",
                (seeded_team,),
            )
            row = cur.fetchone()

    assert row["gameweek"] == 4
    assert row["player_id"] == 1093
    assert row["player_name"] == "Mohamed Salah"
    assert row["outcome_recorded_at"] is None


def test_log_recommendation_writes_null_gameweek_when_none_is_known_yet(seeded_team):
    """seeded_team creates the team but no gameweek_state row -- must not
    raise, unlike log_instinct, since a Recommendation is a byproduct of the
    user's question, not an explicit logging request."""
    with _conn() as conn:
        log_recommendation(conn, seeded_team, 1093, "Mohamed Salah")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek FROM recommendation_log WHERE team_id = %s", (seeded_team,)
            )
            row = cur.fetchone()

    assert row["gameweek"] is None


def test_record_pending_outcomes_fills_in_a_due_recommendation(seeded_team):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (seeded_team, 5, 1093, "Mohamed Salah"),
            )
            log_id = cur.fetchone()["id"]
        conn.commit()

        updated = record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_price_change, outcome_points, outcome_recorded_at "
                "FROM recommendation_log WHERE id = %s",
                (log_id,),
            )
            row = cur.fetchone()

    assert updated == 1
    assert float(row["outcome_price_change"]) == 0.2
    assert row["outcome_points"] == 8
    assert row["outcome_recorded_at"] is not None


def test_record_pending_outcomes_skips_a_row_from_the_same_or_later_gameweek(seeded_team):
    """Only the immediately-prior gameweek's rows are due -- this gameweek's
    own pick hasn't had a chance to play out yet."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (seeded_team, 6, 1093, "Mohamed Salah"),
            )
            log_id = cur.fetchone()["id"]
        conn.commit()

        updated = record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_recorded_at FROM recommendation_log WHERE id = %s", (log_id,)
            )
            row = cur.fetchone()

    assert updated == 0
    assert row["outcome_recorded_at"] is None


def test_record_pending_outcomes_never_backfills_a_row_more_than_one_gameweek_stale(seeded_team):
    """Deliberately NOT "any earlier gameweek": bootstrap-static's
    cost_change_event/event_points are only ever "this event"'s numbers, so
    writing them onto a row from two+ gameweeks back would be wrong data
    (the wrong gameweek's numbers), not just missing data. A skipped
    ingestion cycle means that row's outcome stays NULL forever."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (seeded_team, 4, 1093, "Mohamed Salah"),  # 2 gameweeks stale vs. gameweek=6
            )
            log_id = cur.fetchone()["id"]
        conn.commit()

        updated = record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_recorded_at FROM recommendation_log WHERE id = %s", (log_id,)
            )
            row = cur.fetchone()

    assert updated == 0
    assert row["outcome_recorded_at"] is None


def test_record_pending_outcomes_leaves_an_unmatched_player_id_alone(seeded_team):
    """A player no longer in bootstrap-static's elements (or never matched)
    is left with a NULL outcome rather than guessed at -- no crash either."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (seeded_team, 5, 999999, "Nobody"),
            )
            log_id = cur.fetchone()["id"]
        conn.commit()

        updated = record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_recorded_at FROM recommendation_log WHERE id = %s", (log_id,)
            )
            row = cur.fetchone()

    assert updated == 0
    assert row["outcome_recorded_at"] is None


def test_record_pending_outcomes_does_not_re_update_an_already_recorded_row(seeded_team):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (seeded_team, 5, 1093, "Mohamed Salah"),
            )
            log_id = cur.fetchone()["id"]
        conn.commit()

        record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)
        # A later call for the same prior gameweek, with different (newer)
        # data, must not touch the already-recorded row.
        second_updated = record_pending_outcomes(
            conn, {1093: {"cost_change_event": -5, "event_points": 1}}, gameweek=6
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_price_change, outcome_points FROM recommendation_log WHERE id = %s",
                (log_id,),
            )
            row = cur.fetchone()

    assert second_updated == 0
    assert float(row["outcome_price_change"]) == 0.2
    assert row["outcome_points"] == 8


@pytest.fixture
def test_creator_with_stance():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Outcomes Test Creator", TEST_HANDLE, "medium"),
            )
            creator_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (creator_id, f"https://x.com/{TEST_HANDLE}/status/1", "Buy Salah."),
            )
            content_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, fpl_player_id, stance, "
                " confidence, reason, gameweek) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (creator_id, content_id, "Mohamed Salah", 1093, "buy", 0.7, "Fixtures.", 5),
            )
            stance_id = cur.fetchone()["id"]
        conn.commit()
    yield stance_id
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))  # cascades


def test_record_pending_outcomes_fills_in_a_due_creator_stance(test_creator_with_stance):
    with _conn() as conn:
        updated = record_pending_outcomes(conn, {1093: FAKE_ELEMENT}, gameweek=6)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_price_change, outcome_points, outcome_recorded_at "
                "FROM creator_stances WHERE id = %s",
                (test_creator_with_stance,),
            )
            row = cur.fetchone()

    assert updated == 1
    assert float(row["outcome_price_change"]) == 0.2
    assert row["outcome_points"] == 8
    assert row["outcome_recorded_at"] is not None
