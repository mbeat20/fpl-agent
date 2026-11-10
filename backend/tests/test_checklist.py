"""Issue 23: the deadline screen.

Two kinds of test here, split on purpose.

The item builders are pure functions over `Player` objects, so they are tested
directly with hand-built squads -- that is where the prose lives, and the
prose is the deliverable: an item that states a fact reads differently from
one that issues an instruction, and only an assertion on the string catches
the difference.

`build_checklist` and the route are tested against a real seeded squad,
because the failures worth catching there are wiring failures: the XI/bench
split coming out of `squad_position`, the blank check reaching issue 21's
fixture data, and the route costing nothing to call.

Everything is seeded on a team id and gameweek numbers far outside anything
real. The `gameweeks` table in particular is GLOBAL and holds the live
calendar, so the countdown tests place their own deadlines in 2090 and pass an
explicit `now` alongside them -- otherwise the real GW3 deadline, being
sooner, would win every time.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.checklist import (
    IDLE_BANK,
    bank_item,
    bench_order_item,
    blank_fixture_item,
    build_checklist,
    captain_item,
    countdown_notes,
    deadline_countdown,
    flagged_xi_item,
)
from fpl_coach.models import Player, SquadFinances, Team

TEST_TEAM_ID = 90223
FIXTURE_ID_BASE = 992_000
POOL_ID_BASE = 975_000
# Far outside the 1-38 the real calendar uses, so seeded rows can never be
# confused for -- or ordered against -- a real gameweek.
FIRST_GW = 90
CAL_YEAR = 2090
HOME, AWAY, BLANKED = "ZQA", "ZQB", "ZQC"
TEST_CLUB_IDS = (9911, 9912, 9913)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("DELETE FROM fixtures WHERE id >= %s", (FIXTURE_ID_BASE,))
        conn.execute("DELETE FROM clubs WHERE id = ANY(%s)", (list(TEST_CLUB_IDS),))
        conn.execute("DELETE FROM gameweeks WHERE id >= %s", (FIRST_GW,))
        conn.execute(
            "DELETE FROM fpl_players WHERE id >= %s AND id < %s",
            (POOL_ID_BASE, POOL_ID_BASE + 1000),
        )


def _player(name, position="MID", club=HOME, status="a", **kwargs) -> Player:
    return Player(
        id=abs(hash(name)) % 100_000,
        name=name,
        position=position,
        club=club,
        price=5.0,
        status=status,
        **kwargs,
    )


def _team(**finances) -> Team:
    return Team(
        id=TEST_TEAM_ID,
        name="Checklist Test",
        players=[],
        finances=SquadFinances(**finances) if finances else None,
    )


# ---------------------------------------------------------------------------
# Captain


def test_captain_item_names_the_captain_and_says_which_gameweek_it_is_from():
    """The caveat is the point. FPL only publishes picks once a deadline has
    locked, so a green tick on "captain set" while the armband on record is
    last week's would be the screen's most confident lie."""
    xi = [_player("Zeb Zarkwing", is_captain=True, squad_position=1)]

    item = captain_item(xi, squad_gameweek=91, target=92)

    assert item.state == "ok"
    assert "Zeb Zarkwing" in item.detail
    assert "as of GW91" in item.detail
    assert "not a confirmed pick for GW92" in item.detail
    assert item.players == ["Zeb Zarkwing"]


def test_captain_item_drops_the_caveat_once_the_squad_is_current():
    xi = [_player("Zeb Zarkwing", is_captain=True, squad_position=1)]

    item = captain_item(xi, squad_gameweek=92, target=92)

    assert item.state == "ok"
    assert "confirmed pick" not in item.detail


def test_captain_item_flags_a_captain_who_is_injured():
    xi = [_player("Zeb Zarkwing", status="i", is_captain=True, squad_position=1)]

    item = captain_item(xi, squad_gameweek=92, target=92)

    assert item.state == "attention"
    assert "injured" in item.detail


def test_captain_item_reports_no_armband():
    item = captain_item([_player("Nobody", squad_position=1)], squad_gameweek=92, target=92)

    assert item.state == "attention"
    assert "No player in the XI carries the armband" in item.detail


def test_captain_item_is_unknown_before_any_picks_are_ingested():
    item = captain_item([], squad_gameweek=None, target=92)

    assert item.state == "unknown"
    assert "fpl-ingest" in item.detail


# ---------------------------------------------------------------------------
# Flagged players in the XI


def test_flagged_xi_item_names_every_flagged_player_and_what_is_wrong():
    xi = [
        _player("Fit One", squad_position=1),
        _player("Hurt One", status="i", squad_position=2),
        _player("Banned One", status="s", squad_position=3),
    ]

    item = flagged_xi_item(xi)

    assert item.state == "attention"
    assert "2 players in the XI are flagged" in item.detail
    assert "Hurt One (injured)" in item.detail
    assert "Banned One (suspended)" in item.detail
    assert item.players == ["Hurt One", "Banned One"]


def test_flagged_xi_item_is_ok_when_everyone_is_available():
    item = flagged_xi_item([_player("Fit One"), _player("Fit Two")])

    assert item.state == "ok"
    assert "All 2 players in the XI are listed as available" in item.detail


def test_flagged_xi_item_will_not_call_a_half_checked_xi_clean():
    """The most misleading thing this item could say is "all available" over a
    squad it only half checked. A player with no ingested status is not a
    player who is fine."""
    item = flagged_xi_item([_player("Fit One"), _player("Unknown One", status=None)])

    assert item.state == "unknown"
    assert "1 of 2 players in the XI are listed as available" in item.detail
    assert "Unknown One" in item.detail


def test_flagged_xi_item_still_reports_flags_over_a_half_checked_xi():
    item = flagged_xi_item(
        [_player("Hurt One", status="i"), _player("Unknown One", status=None)]
    )

    assert item.state == "attention"
    assert "Hurt One (injured)" in item.detail
    assert "there may be more" in item.detail


def test_flagged_xi_item_is_unknown_rather_than_ok_when_no_statuses_exist():
    """A squad with no ingested statuses looks identical to a fully fit one.
    Reporting the second would be a clean bill of health drawn from no data at
    all -- exactly the confidently-wrong claim this screen exists to avoid."""
    item = flagged_xi_item([_player("Unknown One", status=None)])

    assert item.state == "unknown"
    assert "No injury statuses have been ingested" in item.detail


# ---------------------------------------------------------------------------
# Bench order


def test_bench_order_item_flags_a_flagged_sub_sitting_ahead_of_a_fit_one():
    """Autosubs come on in order, so an injured first sub means the
    substitution is spent on somebody who cannot play."""
    bench = [
        _player("Sub Keeper", position="GK", squad_position=12),
        _player("Hurt Sub", status="i", squad_position=13),
        _player("Fit Sub", squad_position=14),
        _player("Other Sub", squad_position=15),
    ]

    item = bench_order_item(bench)

    assert item.state == "attention"
    assert "Hurt Sub" in item.detail
    assert "ahead of an available player" in item.detail
    assert item.players == ["Hurt Sub"]


def test_bench_order_item_is_ok_when_the_flagged_sub_is_already_last():
    bench = [
        _player("Sub Keeper", position="GK", squad_position=12),
        _player("Fit Sub", squad_position=13),
        _player("Other Sub", squad_position=14),
        _player("Hurt Sub", status="i", squad_position=15),
    ]

    item = bench_order_item(bench)

    assert item.state == "ok"
    assert "already sit last" in item.detail


def test_bench_order_item_ignores_the_substitute_goalkeeper():
    """A goalkeeper only ever replaces a goalkeeper, so a flagged sub keeper
    at slot 12 is not sitting 'ahead' of the outfield subs in any sense FPL
    recognises. Comparing them would invent a problem the rules make
    impossible."""
    bench = [
        _player("Hurt Keeper", position="GK", status="i", squad_position=12),
        _player("Fit Sub", squad_position=13),
        _player("Other Sub", squad_position=14),
        _player("Third Sub", squad_position=15),
    ]

    item = bench_order_item(bench)

    assert item.state == "ok"
    assert "Hurt Keeper" not in item.detail


def test_bench_order_item_is_unknown_without_enough_bench_to_order():
    assert bench_order_item([]).state == "unknown"
    assert bench_order_item([_player("Only Sub", squad_position=12)]).state == "unknown"


# ---------------------------------------------------------------------------
# Bank


def test_bank_item_flags_money_sitting_idle():
    item = bank_item(_team(bank=IDLE_BANK + 0.5, selling_prices_exact=True))

    assert item.state == "attention"
    assert "GBP 1.5m is sitting in the bank" in item.detail


def test_bank_item_is_ok_below_the_threshold():
    item = bank_item(_team(bank=0.4, selling_prices_exact=True))

    assert item.state == "ok"
    assert "GBP 0.4m in the bank" in item.detail
    assert "upper bound" not in item.detail


def test_bank_item_states_the_selling_price_caveat_when_it_applies():
    """Issue 20's load-bearing limitation. A bare bank figure reads as a
    complete budget, and it is not one whenever a squad player has risen since
    purchase -- FPL will not tell us by how much without an authenticated
    session."""
    item = bank_item(_team(bank=0.4, selling_prices_exact=False))

    assert item.state == "ok"
    assert "upper bound" in item.detail


def test_bank_item_is_unknown_before_ingestion():
    assert bank_item(_team()).state == "unknown"


# ---------------------------------------------------------------------------
# The countdown's self-reported reliability


def test_countdown_note_warns_that_a_first_kickoff_countdown_runs_long():
    notes = countdown_notes("first_kickoff", planning=92, gameweek=92)

    assert len(notes) == 1
    assert "90 minutes EARLIER" in notes[0]


def test_countdown_note_says_when_a_gameweek_is_already_under_way():
    """The mid-gameweek window: planning_gameweek resolves to the gameweek in
    progress, whose deadline went yesterday. Silence here would let the screen
    imply a locked team can still be changed."""
    notes = countdown_notes("deadlines", planning=92, gameweek=93)

    assert any("GW92 is under way" in note for note in notes)
    assert any("GW93" in note for note in notes)


def test_countdown_note_is_silent_when_the_two_gameweeks_agree():
    assert countdown_notes("deadlines", planning=92, gameweek=92) == []


# ---------------------------------------------------------------------------
# Against a real database


@pytest.fixture
def seeded_squad():
    """A 15-player squad with an XI, a bench, an armband, a bank and a
    fixture calendar of its own.

    ZQA and ZQB play each other in every seeded gameweek. ZQC never plays --
    the blank the checklist has to catch.
    """
    _cleanup()
    deadline = datetime(CAL_YEAR, 5, 1, 17, 30, tzinfo=timezone.utc)

    squad = [
        # (name, position, club, squad_position, status, captain)
        ("Keeper One", "GK", HOME, 1, "a", False),
        ("Back One", "DEF", HOME, 2, "a", False),
        ("Back Two", "DEF", HOME, 3, "a", False),
        ("Back Three", "DEF", AWAY, 4, "a", False),
        ("Back Four", "DEF", AWAY, 5, "a", False),
        ("Mid One", "MID", AWAY, 6, "a", True),
        ("Mid Two", "MID", AWAY, 7, "a", False),
        ("Mid Three", "MID", HOME, 8, "a", False),
        ("Mid Four", "MID", HOME, 9, "a", False),
        ("Front One", "FWD", AWAY, 10, "a", False),
        # The blank: ZQC has no fixture in any seeded gameweek.
        ("Front Two", "FWD", BLANKED, 11, "a", False),
        ("Keeper Two", "GK", HOME, 12, "a", False),
        ("Sub One", "DEF", AWAY, 13, "a", False),
        ("Sub Two", "MID", HOME, 14, "a", False),
        ("Sub Three", "FWD", AWAY, 15, "a", False),
    ]
    squad_total = round(5.0 * len(squad), 1)
    bank = 0.4

    with _conn() as conn:
        conn.execute(
            "INSERT INTO teams (id, name, bank, team_value, squad_gameweek) "
            "VALUES (%s, %s, %s, %s, %s)",
            (TEST_TEAM_ID, "Checklist Test", bank, round(squad_total + bank, 1), FIRST_GW),
        )
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, FIRST_GW),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clubs (id, name, short_name) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET short_name = EXCLUDED.short_name",
                [
                    (9911, "Zed Quay", HOME),
                    (9912, "Zed Quarry", AWAY),
                    (9913, "Zed Quiet", BLANKED),
                ],
            )
            cur.executemany(
                "INSERT INTO gameweeks (id, name, deadline_time) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET deadline_time = EXCLUDED.deadline_time",
                [
                    (FIRST_GW + n, f"Gameweek {FIRST_GW + n}", deadline + timedelta(days=7 * n))
                    for n in range(3)
                ],
            )
            cur.executemany(
                "INSERT INTO fixtures (id, gameweek, home_team, away_team, kickoff_time, "
                "home_difficulty, away_difficulty) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        FIXTURE_ID_BASE + n,
                        FIRST_GW + n,
                        HOME,
                        AWAY,
                        deadline + timedelta(days=7 * n, hours=2),
                        2,
                        3,
                    )
                    for n in range(3)
                ],
            )
            cur.executemany(
                "INSERT INTO players (team_id, name, position, club, price, status, "
                "squad_position, is_captain) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (TEST_TEAM_ID, name, pos, club, 5.0, status, position, captain)
                    for name, pos, club, position, status, captain in squad
                ],
            )
        conn.commit()
        yield conn

    _cleanup()


def _item(checklist, key):
    return next(item for item in checklist.items if item.key == key)


def test_build_checklist_splits_the_xi_from_the_bench_by_squad_position(seeded_squad):
    """The wiring test that matters: everything else keys off this split, and
    a bench player counted into the XI would put a legitimately benched
    injury on the screen as an XI problem."""
    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    assert _item(checklist, "captain").detail.startswith("Mid One has the armband")
    assert "All 11 players in the XI" in _item(checklist, "flagged_in_xi").detail
    # Sub One / Sub Two / Sub Three are the outfield bench, in order; the
    # substitute keeper is excluded.
    assert (
        "Sub One then Sub Two then Sub Three" in _item(checklist, "bench_order").detail
    )


def test_build_checklist_catches_a_blank_gameweek_in_the_xi(seeded_squad):
    """The failure the AI card above cannot catch: a player who is fit, in
    form, and playing nobody."""
    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    item = _item(checklist, "blank_fixture")
    assert item.state == "attention"
    assert item.players == ["Front Two"]
    assert f"no fixture in GW{FIRST_GW}" in item.detail
    assert BLANKED in item.detail


def test_an_unscheduled_fixture_is_not_reported_as_a_blank(seeded_squad):
    """Ticket 21 keeps "no fixture" and "no fixture scheduled yet" apart, and
    this screen must not put them back together. A postponement awaiting a
    slot may still land in this gameweek; calling it a blank would tell the
    user their player scores nothing when he may well play."""
    with seeded_squad.cursor() as cur:
        cur.execute(
            "INSERT INTO fixtures (id, gameweek, home_team, away_team) "
            "VALUES (%s, NULL, %s, %s)",
            (FIXTURE_ID_BASE + 50, BLANKED, HOME),
        )
    seeded_squad.commit()

    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    item = _item(checklist, "blank_fixture")
    assert item.state == "unknown"
    assert item.players == ["Front Two"]
    assert "unscheduled, not a blank" in item.detail


def test_build_checklist_counts_down_to_the_next_unpassed_deadline(seeded_squad):
    """Wall-clock, from the deadline calendar -- not from whichever gameweek
    ingestion last resolved."""
    now = datetime(CAL_YEAR, 5, 1, 15, 30, tzinfo=timezone.utc)  # two hours before GW90

    checklist = build_checklist(seeded_squad, TEST_TEAM_ID, now=now)

    assert checklist.countdown.gameweek == FIRST_GW
    assert checklist.countdown.source == "deadlines"
    assert checklist.countdown.seconds_remaining == 2 * 60 * 60
    assert checklist.target_gameweek == FIRST_GW


def test_build_checklist_moves_on_once_a_deadline_has_passed(seeded_squad):
    """The window the ticket names: the gameweek being played is locked, and
    the countdown must already be running on the next one.

    `planning_gameweek` still says GW90 here (that is the gameweek in
    progress), so this is also the test that the countdown does not follow
    it."""
    now = datetime(CAL_YEAR, 5, 2, tzinfo=timezone.utc)  # after GW90, before GW91

    checklist = build_checklist(seeded_squad, TEST_TEAM_ID, now=now)

    assert checklist.countdown.gameweek == FIRST_GW + 1
    assert checklist.countdown.planning_gameweek == FIRST_GW
    assert f"GW{FIRST_GW} is under way" in checklist.countdown.note
    assert checklist.target_gameweek == FIRST_GW + 1


def test_build_checklist_counts_the_items_needing_attention(seeded_squad):
    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    attention = [item.state for item in checklist.items].count("attention")
    assert checklist.attention_count == attention
    assert checklist.attention_count >= 1  # the blank, at minimum


def test_price_risk_covers_only_the_users_own_players(seeded_squad):
    """Scoped to the squad, and -- crucially -- not to the top ten of the
    global list. A squad player heading for a fall is rarely among the ten
    steepest falls in the game, so filtering `predict_price_changes` output
    would have quietly reported no risk at all."""
    with seeded_squad.cursor() as cur:
        cur.execute(
            "INSERT INTO fpl_players (id, name, full_name, position, club, price, "
            "ownership, transfers_in_event, transfers_out_event) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (POOL_ID_BASE + 1, "Front Two", "Front Two", "FWD", BLANKED, 5.0, 1.0, 0, 5000),
        )
        # A pool player who is NOT in the squad, falling far harder.
        cur.execute(
            "INSERT INTO fpl_players (id, name, full_name, position, club, price, "
            "ownership, transfers_in_event, transfers_out_event) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (POOL_ID_BASE + 2, "Not Mine", "Not Mine", "FWD", BLANKED, 5.0, 1.0, 0, 900_000),
        )
        cur.execute(
            "UPDATE players SET fpl_element_id = %s WHERE team_id = %s AND name = %s",
            (POOL_ID_BASE + 1, TEST_TEAM_ID, "Front Two"),
        )
    seeded_squad.commit()

    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    falling = {p.player_name for p in checklist.price_risk.falling}
    assert "Front Two" in falling
    assert "Not Mine" not in falling


def test_deadline_countdown_reports_no_calendar_rather_than_guessing(seeded_squad):
    """Past the last seeded deadline every real one is long gone too, so this
    is the end-of-season shape: no countdown, and a note saying why."""
    countdown = deadline_countdown(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR + 10, 1, 1, tzinfo=timezone.utc)
    )

    assert countdown.gameweek is None
    assert countdown.seconds_remaining is None
    assert "passed" in countdown.note


# ---------------------------------------------------------------------------
# The route


class _RefusingReasoning:
    """Fails the test rather than the request if anything reaches Claude."""

    def reason(self, prompt, tools, system="", validate=None, history=None):
        raise AssertionError("the checklist route must not make a reasoning call")


class _RefusingFPLClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("the checklist route must not make a live FPL API call")


def test_checklist_route_is_free_to_call(seeded_squad, monkeypatch):
    """The acceptance criterion stated as a test: no Claude call and no live
    FPL API call. Opening the dashboard to watch the countdown must not be a
    billed event, and refreshing it must not rate-limit the FPL API."""
    monkeypatch.setattr("fpl_coach.api.routes.MY_TEAM_ID", TEST_TEAM_ID)
    monkeypatch.setattr("fpl_coach.api.routes.FPLClient", _RefusingFPLClient)
    app.dependency_overrides[get_reasoning] = _RefusingReasoning
    try:
        with TestClient(app) as client:
            response = client.get("/team/checklist")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert {item["key"] for item in body["items"]} == {
        "captain",
        "flagged_in_xi",
        "blank_fixture",
        "bench_order",
        "bank",
    }
    assert body["squad_gameweek"] == FIRST_GW


def test_checklist_route_still_answers_before_any_ingestion(client, seeded_team):
    """conftest's `seeded_team` is three players with no squad_position, no
    statuses, no bank -- the shape the dashboard has before ingestion has ever
    run. Every item should read `unknown`, and none should crash."""
    response = client.get("/team/checklist")

    assert response.status_code == 200
    body = response.json()
    assert [i["state"] for i in body["items"]] == ["unknown"] * len(body["items"])
    assert body["unknown_count"] == len(body["items"])
    assert body["attention_count"] == 0
    assert body["price_risk"] == {"rising": [], "falling": []}


def test_no_checklist_item_instructs_the_user(seeded_squad):
    """The ticket's judgement call, enforced. Each item states the fact and
    leaves the decision alone -- preferences.md is emphatic that the final
    call is the user's, and the AI suggestion card is where advice belongs."""
    checklist = build_checklist(
        seeded_squad, TEST_TEAM_ID, now=datetime(CAL_YEAR, 4, 1, tzinfo=timezone.utc)
    )

    # Imperatives aimed at the squad. "Run fpl-ingest" is deliberately not in
    # this set: that is an instruction about the app, not about the team.
    forbidden = (" bench ", "you should", "you must", "consider ", "swap ", "replace ")
    for item in checklist.items:
        lowered = f" {item.detail.lower()} "
        assert not any(phrase in lowered for phrase in forbidden), item.detail
