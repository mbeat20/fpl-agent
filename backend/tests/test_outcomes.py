"""Issue 10 / ADR-0012, and issue 24's rewrite of it.

The failure ticket 24 exists to fix: outcomes were resolved from
bootstrap-static's `cost_change_event` / `event_points`, which are only ever
*this event's* numbers. That forced a one-gameweek window, so any skipped
ingestion cycle (ADR-0002: manual, not scheduled) let a row's window pass and
its outcome stayed NULL forever. The history ADR-0001 defers creator
trust-scoring on the strength of was being lost in real time.

`resolve_outcome` is a pure function over one player's history rows, so the
arithmetic is tested without a database. The catch-up itself needs one.
"""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.fixtures import upcoming_gameweek
from fpl_coach.outcomes import backfill_outcomes, log_recommendation, resolve_outcome

TEST_HANDLE = "outcomes_test_creator"
# Far outside anything real, and outside the ranges other test files claim.
ELEMENT_ID = 978_001
OTHER_ELEMENT_ID = 978_002
FIXTURE_ID_BASE = 994_000
FIRST_GW = 70


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _history(gameweek, points, value, element_id=ELEMENT_ID):
    return {
        "fpl_player_id": element_id,
        "gameweek": gameweek,
        "total_points": points,
        "value": value,
    }


# ---------------------------------------------------------------------------
# The arithmetic


def test_points_and_price_come_from_the_rows_own_gameweek():
    """The heart of it. A row from GW70 is scored on GW70's numbers even
    though GW72 is the newest data we hold -- which is precisely what reading
    a "this event" field could never do."""
    history = [
        _history(70, 12, 9.5),
        _history(71, 2, 9.7),
        _history(72, 6, 9.8),
    ]

    change, points = resolve_outcome(history, 70)

    assert points == 12
    assert change == pytest.approx(0.2)  # 9.7 - 9.5, the move across GW70


def test_a_double_gameweek_sums_both_fixtures():
    history = [
        _history(70, 5, 9.5),
        _history(70, 8, 9.5),
        _history(71, 1, 9.6),
    ]

    _, points = resolve_outcome(history, 70)

    assert points == 13


def test_not_featuring_is_a_real_zero_when_we_hold_later_history():
    """FPL emits a history row per fixture even at nil minutes, so a gap with
    later data around it means the player genuinely had no fixture -- 0
    points, not missing data."""
    history = [_history(70, 4, 9.5), _history(72, 6, 9.6)]

    change, points = resolve_outcome(history, 71)

    assert points == 0
    assert change == pytest.approx(0.1)


def test_a_player_we_hold_nothing_for_stays_unknown():
    assert resolve_outcome([], 70) == (None, None)


def test_history_that_does_not_reach_the_gameweek_stays_unknown():
    """The distinction that must not be collapsed: "did not feature" and "not
    fetched this far forward yet" look identical from a missing row, and only
    the first is a nil."""
    history = [_history(68, 4, 9.5), _history(69, 2, 9.5)]

    assert resolve_outcome(history, 70) == (None, None)


def test_price_change_is_none_when_nothing_later_is_on_record():
    """A price movement with no later reading is unknown, not zero. At the
    end of a season the points still resolve and the price does not."""
    history = [_history(70, 9, 9.5)]

    change, points = resolve_outcome(history, 70)

    assert points == 9
    assert change is None


def test_price_change_survives_a_gap_in_the_history():
    history = [_history(70, 9, 9.5), _history(73, 4, 9.9)]

    change, _ = resolve_outcome(history, 70)

    assert change == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Logging (unchanged by issue 24, kept as regression cover)


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


def test_log_recommendation_uses_an_explicit_gameweek_over_the_current_one(seeded_team):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 4),
        )
        log_recommendation(conn, seeded_team, 1093, "Mohamed Salah", gameweek=9)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek FROM recommendation_log WHERE team_id = %s", (seeded_team,)
            )
            assert cur.fetchone()["gameweek"] == 9


def test_log_recommendation_falls_back_to_the_fixture_derived_gameweek(seeded_team):
    with _conn() as conn:
        conn.execute("DELETE FROM gameweek_state WHERE team_id = %s", (seeded_team,))
        expected = upcoming_gameweek(conn)
        log_recommendation(conn, seeded_team, 1093, "Mohamed Salah")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek FROM recommendation_log WHERE team_id = %s", (seeded_team,)
            )
            assert cur.fetchone()["gameweek"] == expected


# ---------------------------------------------------------------------------
# The catch-up


def _seed_history(conn, rows):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO player_gameweek_history "
            "(fpl_player_id, gameweek, minutes, total_points, value) "
            "VALUES (%s, %s, 90, %s, %s) "
            "ON CONFLICT (fpl_player_id, gameweek) DO UPDATE SET "
            "total_points = EXCLUDED.total_points, value = EXCLUDED.value",
            rows,
        )


@pytest.fixture
def played_season(seeded_team):
    """Four finished gameweeks of fixtures, and one player's history across
    them. The fixtures matter: `latest_finished_gameweek` is derived from
    them, and it is what bounds the catch-up."""
    with _conn() as conn:
        conn.execute("DELETE FROM fixtures WHERE id >= %s", (FIXTURE_ID_BASE,))
        conn.execute(
            "DELETE FROM player_gameweek_history WHERE fpl_player_id IN (%s, %s)",
            (ELEMENT_ID, OTHER_ELEMENT_ID),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fixtures (id, gameweek, home_team, away_team, kickoff_time) "
                "VALUES (%s, %s, 'ZOA', 'ZOB', now() - interval '10 days')",
                [(FIXTURE_ID_BASE + n, FIRST_GW + n) for n in range(4)],
            )
            _seed_history(
                cur.connection,
                [
                    (ELEMENT_ID, FIRST_GW, 12, 9.5),
                    (ELEMENT_ID, FIRST_GW + 1, 2, 9.7),
                    (ELEMENT_ID, FIRST_GW + 2, 8, 9.8),
                    (ELEMENT_ID, FIRST_GW + 3, 1, 9.8),
                ],
            )
        conn.commit()
        yield conn

    with _conn() as conn:
        conn.execute("DELETE FROM fixtures WHERE id >= %s", (FIXTURE_ID_BASE,))
        conn.execute(
            "DELETE FROM player_gameweek_history WHERE fpl_player_id IN (%s, %s)",
            (ELEMENT_ID, OTHER_ELEMENT_ID),
        )


def _log(conn, team_id, gameweek, element_id=ELEMENT_ID):
    log_recommendation(conn, team_id, element_id, "Test Player", gameweek=gameweek)


def test_skipping_three_gameweeks_then_running_resolves_every_outstanding_row(
    played_season, seeded_team
):
    """The ticket's headline acceptance criterion. Three gameweeks go by with
    no ingestion; the next run resolves all of them, each against its OWN
    gameweek's points -- not the latest gameweek's, which is what the old
    implementation would have written had it not refused outright."""
    for gameweek in (FIRST_GW, FIRST_GW + 1, FIRST_GW + 2):
        _log(played_season, seeded_team, gameweek)

    result = backfill_outcomes(played_season)

    assert result.recommendations_resolved == 3
    assert result.unresolvable == 0

    with played_season.cursor() as cur:
        cur.execute(
            "SELECT gameweek, outcome_points, outcome_price_change FROM recommendation_log "
            "WHERE team_id = %s ORDER BY gameweek",
            (seeded_team,),
        )
        rows = cur.fetchall()

    assert [r["outcome_points"] for r in rows] == [12, 2, 8]
    assert [float(r["outcome_price_change"]) for r in rows] == [
        pytest.approx(0.2),
        pytest.approx(0.1),
        pytest.approx(0.0),
    ]


def test_resolution_is_driven_by_rows_lacking_an_outcome_not_by_this_run(
    played_season, seeded_team
):
    """[[project_ingestion_backfill_pattern]]: keying off what changed in the
    current cycle permanently skips everything that already existed. Nothing
    about this run touches the row -- it is found because its outcome is
    NULL."""
    _log(played_season, seeded_team, FIRST_GW)

    assert backfill_outcomes(played_season).recommendations_resolved == 1
    # Second run finds nothing: already resolved, so it is no longer pending.
    assert backfill_outcomes(played_season).recommendations_resolved == 0


def test_a_row_for_an_unfinished_gameweek_is_left_alone(played_season, seeded_team):
    """A gameweek still being played has no outcome yet, and scoring it now
    would record a nil for a match that has not happened."""
    _log(played_season, seeded_team, FIRST_GW + 9)

    result = backfill_outcomes(played_season)

    assert result.recommendations_resolved == 0
    with played_season.cursor() as cur:
        cur.execute(
            "SELECT outcome_recorded_at FROM recommendation_log WHERE team_id = %s",
            (seeded_team,),
        )
        assert cur.fetchone()["outcome_recorded_at"] is None


def test_a_player_with_no_history_is_left_null_and_counted(played_season, seeded_team):
    """Unknown stays NULL rather than being guessed at -- and is reported, so
    silence is never mistaken for completeness."""
    _log(played_season, seeded_team, FIRST_GW, element_id=OTHER_ELEMENT_ID)

    result = backfill_outcomes(played_season)

    assert result.recommendations_resolved == 0
    assert result.unresolvable == 1
    with played_season.cursor() as cur:
        cur.execute(
            "SELECT outcome_points, outcome_recorded_at FROM recommendation_log "
            "WHERE team_id = %s",
            (seeded_team,),
        )
        row = cur.fetchone()
    assert row["outcome_points"] is None and row["outcome_recorded_at"] is None


def test_an_already_recorded_row_is_not_touched_again(played_season, seeded_team):
    _log(played_season, seeded_team, FIRST_GW)
    backfill_outcomes(played_season)

    with played_season.cursor() as cur:
        cur.execute(
            "SELECT outcome_recorded_at FROM recommendation_log WHERE team_id = %s",
            (seeded_team,),
        )
        first = cur.fetchone()["outcome_recorded_at"]

    backfill_outcomes(played_season)

    with played_season.cursor() as cur:
        cur.execute(
            "SELECT outcome_recorded_at FROM recommendation_log WHERE team_id = %s",
            (seeded_team,),
        )
        assert cur.fetchone()["outcome_recorded_at"] == first


def test_a_missing_history_is_fetched_on_demand_when_a_client_is_available(
    played_season, seeded_team
):
    """What makes a row from before the minutes cache existed resolvable at
    all: the backfill fetches the players its own unresolved rows name."""

    class _StubClient:
        def __init__(self):
            self.calls = []

        def get_element_summary(self, element_id):
            self.calls.append(element_id)
            return {
                "history": [
                    {"round": FIRST_GW, "minutes": 90, "total_points": 7, "value": 60},
                    {"round": FIRST_GW + 1, "minutes": 90, "total_points": 3, "value": 61},
                ]
            }

    _log(played_season, seeded_team, FIRST_GW, element_id=OTHER_ELEMENT_ID)
    client = _StubClient()

    result = backfill_outcomes(played_season, client)

    assert client.calls == [OTHER_ELEMENT_ID]
    assert result.players_fetched == 1
    assert result.recommendations_resolved == 1
    with played_season.cursor() as cur:
        cur.execute(
            "SELECT outcome_points, outcome_price_change FROM recommendation_log "
            "WHERE team_id = %s",
            (seeded_team,),
        )
        row = cur.fetchone()
    assert row["outcome_points"] == 7
    assert float(row["outcome_price_change"]) == pytest.approx(0.1)


def test_a_player_already_cached_is_not_refetched(played_season, seeded_team):
    class _ExplodingClient:
        def get_element_summary(self, element_id):
            raise AssertionError("should not fetch a player we can already answer for")

    _log(played_season, seeded_team, FIRST_GW)

    assert backfill_outcomes(played_season, _ExplodingClient()).recommendations_resolved == 1


# ---------------------------------------------------------------------------
# Creator Stances resolve on the same basis


@pytest.fixture
def stance_creator(played_season):
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, 'high') "
                "RETURNING id",
                ("Outcomes Test", TEST_HANDLE),
            )
            creator_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, 'https://example.test/o', 'text') RETURNING id",
                (creator_id,),
            )
            content_id = cur.fetchone()["id"]
        conn.commit()
        yield conn, content_id

    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))


def test_creator_stance_outcomes_resolve_on_the_same_basis(stance_creator):
    """AC: Creator Stance outcomes resolve exactly as recommendation outcomes
    do -- same source, same catch-up, same "however old" behaviour."""
    conn, content_id = stance_creator
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO creator_stances (creator_id, source_content_id, player_name, "
            "fpl_player_id, stance, confidence, reason, gameweek) "
            "SELECT creator_id, id, 'Test Player', %s, 'buy', 0.8, 'because', %s "
            "FROM creator_content WHERE id = %s",
            (ELEMENT_ID, FIRST_GW + 1, content_id),
        )
    conn.commit()

    result = backfill_outcomes(conn)

    assert result.stances_resolved == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT outcome_points, outcome_price_change FROM creator_stances "
            "WHERE source_content_id = %s",
            (content_id,),
        )
        row = cur.fetchone()
    assert row["outcome_points"] == 2
    assert float(row["outcome_price_change"]) == pytest.approx(0.1)
