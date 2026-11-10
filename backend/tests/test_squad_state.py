"""Issue 20: the FPL state the model was already being told to reason about.

`fpl_rules.py` has instructed the model to check affordability "from the
selling price of the player out plus the money in the bank" since ticket 01,
against a schema with no bank column. These tests cover the three things that
closes: money, chips, and minutes.

Seeds its own team id, never MY_TEAM_ID -- conftest's seeded_team deletes the
real team row and cascade-wipes real state.
"""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.minutes import recent_minutes, refresh_squad_minutes
from fpl_coach.squad_state import chip_status, rotation_risk, sellable_value
from fpl_coach.tools.get_my_team import get_my_team

TEST_TEAM_ID = 90220
TEST_ELEMENT_IDS = (90101, 90102)

# Mirrors bootstrap's real shape: a fresh set of chips per half-season, which
# is why the same name appears twice with different windows.
CHIP_CATALOGUE = [
    {"name": "wildcard", "chip_type": "transfer", "start_event": 1, "stop_event": 19},
    {"name": "wildcard", "chip_type": "transfer", "start_event": 20, "stop_event": 38},
    {"name": "freehit", "chip_type": "transfer", "start_event": 1, "stop_event": 19},
    {"name": "bboost", "chip_type": "team", "start_event": 1, "stop_event": 19},
]


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute(
            "DELETE FROM player_gameweek_history WHERE fpl_player_id = ANY(%s)",
            (list(TEST_ELEMENT_IDS),),
        )


def _seed_team(bank, team_value, player_prices, statuses=None, minutes=None):
    statuses = statuses or ["a"] * len(player_prices)
    minutes = minutes or [None] * len(player_prices)
    _cleanup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name, bank, team_value, squad_gameweek) "
                "VALUES (%s, %s, %s, %s, %s)",
                (TEST_TEAM_ID, "Finance Test Team", bank, team_value, 3),
            )
            for i, (price, status, mins) in enumerate(zip(player_prices, statuses, minutes)):
                cur.execute(
                    "INSERT INTO players "
                    "(team_id, name, position, club, price, status, minutes, starts, "
                    " squad_position, is_captain, is_vice_captain, multiplier) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (TEST_TEAM_ID, f"Player {i}", "MID", "LIV", price, status, mins,
                     1, i + 1, i == 0, i == 1, 2 if i == 0 else 1),
                )
        conn.commit()


@pytest.fixture
def cleanup():
    yield
    _cleanup()


# --- money -------------------------------------------------------------------


def test_squad_selling_value_subtracts_the_bank_from_fpl_team_value(cleanup):
    """The bug this exists to prevent: FPL's `value` is team value, squad PLUS
    bank. At GW1 every squad in the game reads value=100.0 with 0.5 banked and
    a 99.5m squad -- treating `value` as the squad alone overstates it by
    exactly the banked amount, and every affordability answer inherits that."""
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0, 49.5])

    with _conn() as conn:
        finances = sellable_value(conn, TEST_TEAM_ID)

    assert finances.team_value == 100.0
    assert finances.bank == 0.5
    assert finances.squad_selling_value == 99.5


def test_affordability_is_exact_when_nobody_has_changed_price(cleanup):
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0, 49.5])

    with _conn() as conn:
        finances = sellable_value(conn, TEST_TEAM_ID)

    assert finances.selling_prices_exact is True
    assert "exact" in finances.note


def test_a_price_gap_is_reported_as_an_upper_bound_not_hidden(cleanup):
    """Listed prices total more than the squad's real selling value, so at
    least one player sells for less than his listed price. We know the
    aggregate but not who -- the model must be told to treat listed price as a
    ceiling rather than quietly assuming the transfer fits."""
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0, 49.7])

    with _conn() as conn:
        finances = sellable_value(conn, TEST_TEAM_ID)

    assert finances.selling_prices_exact is False
    assert "UPPER BOUND" in finances.note
    assert "0.2" in finances.note


def test_no_ingested_budget_says_unknown_rather_than_guessing(cleanup):
    _seed_team(bank=None, team_value=None, player_prices=[50.0])

    with _conn() as conn:
        finances = sellable_value(conn, TEST_TEAM_ID)

    assert finances.bank is None
    assert finances.selling_prices_exact is False
    assert "unknown" in finances.note


# --- chips -------------------------------------------------------------------


def test_a_played_chip_leaves_its_own_half_but_not_the_other(cleanup):
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0])
    with _conn() as conn:
        conn.execute(
            "INSERT INTO chips_used (team_id, name, gameweek) VALUES (%s, %s, %s)",
            (TEST_TEAM_ID, "wildcard", 4),
        )
        conn.commit()
        status = chip_status(conn, TEST_TEAM_ID, CHIP_CATALOGUE, planning_gameweek=6)

    assert [(c.name, c.played_in_gameweek) for c in status.used] == [("wildcard", 4)]
    windows = [(c.name, c.available_from_gameweek) for c in status.remaining]
    # The first-half wildcard is spent; the second-half one is not.
    assert ("wildcard", 1) not in windows
    assert ("wildcard", 20) in windows


def test_playable_now_respects_each_chips_own_window(cleanup):
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0])
    with _conn() as conn:
        status = chip_status(conn, TEST_TEAM_ID, CHIP_CATALOGUE, planning_gameweek=25)

    # In GW25 only the second-half wildcard is in window.
    assert status.playable_now == ["wildcard"]
    second_half = next(c for c in status.remaining if c.available_from_gameweek == 20)
    assert second_half.playable_now is True


def test_an_unused_chip_set_reports_every_chip_as_remaining(cleanup):
    _seed_team(bank=0.5, team_value=100.0, player_prices=[50.0])
    with _conn() as conn:
        status = chip_status(conn, TEST_TEAM_ID, CHIP_CATALOGUE, planning_gameweek=2)

    assert status.used == []
    assert len(status.remaining) == len(CHIP_CATALOGUE)
    assert set(status.playable_now) == {"wildcard", "freehit", "bboost"}


# --- minutes -----------------------------------------------------------------


def test_rotation_risk_flags_a_fit_player_who_is_not_starting():
    # 100 minutes across 4 gameweeks = 25 a week, well under FPL's 60-minute
    # full-appearance threshold.
    assert rotation_risk(minutes=100, played=4, status="a") is True


def test_rotation_risk_ignores_an_injured_player():
    """An injured player's low minutes are already explained by `status`.
    Flagging them too would bury the case that is actually surprising."""
    assert rotation_risk(minutes=100, played=4, status="i") is False


def test_rotation_risk_needs_enough_season_to_judge():
    """One blank gameweek in GW1 must not flag the entire squad."""
    assert rotation_risk(minutes=45, played=1, status="a") is False


def test_a_nailed_starter_is_not_flagged():
    assert rotation_risk(minutes=360, played=4, status="a") is False


def test_get_my_team_exposes_the_xi_bench_order_and_armband(cleanup):
    _seed_team(
        bank=0.5,
        team_value=100.0,
        player_prices=[50.0, 30.0, 19.5],
        minutes=[270, 270, 100],
    )

    with _conn() as conn:
        team = get_my_team(conn, TEST_TEAM_ID)

    assert [p.squad_position for p in team.players] == [1, 2, 3]
    assert team.players[0].is_captain is True
    assert team.players[1].is_vice_captain is True
    assert team.players[0].multiplier == 2
    assert team.squad_gameweek == 3
    assert team.finances is not None


# --- the minutes cache -------------------------------------------------------


class _CountingClient:
    """Counts element-summary calls, so the ticket's "do not fan out across
    the pool" constraint is asserted rather than assumed."""

    def __init__(self):
        self.calls: list[int] = []

    def get_element_summary(self, element_id: int) -> dict:
        self.calls.append(element_id)
        return {
            "history": [
                {"round": 1, "minutes": 90, "starts": 1, "total_points": 6,
                 "was_home": True, "opponent_team": 2},
                {"round": 2, "minutes": 20, "starts": 0, "total_points": 1,
                 "was_home": False, "opponent_team": 3},
            ]
        }


def test_minutes_are_fetched_once_then_served_from_cache(cleanup):
    client = _CountingClient()
    with _conn() as conn:
        first = refresh_squad_minutes(conn, client, list(TEST_ELEMENT_IDS), 2, {2: "MCI"})
        second = refresh_squad_minutes(conn, client, list(TEST_ELEMENT_IDS), 2, {2: "MCI"})

    assert first == 2
    # The whole point: a re-run inside the same gameweek costs zero HTTP calls.
    assert second == 0
    assert len(client.calls) == 2


def test_a_new_finished_gameweek_invalidates_the_cache(cleanup):
    client = _CountingClient()
    with _conn() as conn:
        refresh_squad_minutes(conn, client, [TEST_ELEMENT_IDS[0]], 2, {})
        refetched = refresh_squad_minutes(conn, client, [TEST_ELEMENT_IDS[0]], 3, {})

    assert refetched == 1


def test_recent_minutes_come_back_newest_first(cleanup):
    client = _CountingClient()
    with _conn() as conn:
        refresh_squad_minutes(conn, client, [TEST_ELEMENT_IDS[0]], 2, {2: "MCI", 3: "ARS"})
        rows = recent_minutes(conn, TEST_ELEMENT_IDS[0])

    assert [r["gameweek"] for r in rows] == [2, 1]
    assert rows[0]["minutes"] == 20


def test_one_players_failure_does_not_abort_the_rest(cleanup):
    """Minutes are an enrichment. A single element-summary 500 must not take
    down an ingestion run that has already written the squad."""

    class _FlakyClient(_CountingClient):
        def get_element_summary(self, element_id: int) -> dict:
            if element_id == TEST_ELEMENT_IDS[0]:
                raise RuntimeError("FPL said no")
            return super().get_element_summary(element_id)

    with _conn() as conn:
        fetched = refresh_squad_minutes(conn, _FlakyClient(), list(TEST_ELEMENT_IDS), 2, {})

    assert fetched == 1
