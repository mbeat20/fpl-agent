"""Issue 22: legality enforcement that refuses.

The failure this guards against is one that already reached the user:
`recommend_transfers` called `get_player` twice and computed a `gain_signal`
without ever comparing the two players' positions, so a
midfielder-out-for-a-forward move -- which FPL will not accept -- was priced,
ranked, given creator opinions on both sides, and returned as a legitimate
comparison. `fpl_rules.py`'s prose briefing was written because of that, and
its own docstring says a prompt is the wrong place for an invariant.

Seeds a full, legal 15-player squad of its own on a team id that is not
MY_TEAM_ID, so the shape rules can be exercised properly -- conftest's
`seeded_team` holds three players and would fail the composition check on
every move for reasons that have nothing to do with the move.
"""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.models import SquadMoveVerdict, TransferComparison
from fpl_coach.squad_rules import SQUAD_SIZE, free_transfers_at
from fpl_coach.tools.recommend_transfers import recommend_transfers
from fpl_coach.tools.validate_squad_move import validate_incoming, validate_squad_move

TEST_TEAM_ID = 90222
POOL_ID_BASE = 970_000
PLANNING_GW = 50

# A legal 15: 2 GK, 5 DEF, 5 MID, 3 FWD, at most 3 from any one club.
# "ZKC" deliberately supplies exactly three, so the club limit is one move
# away from being breached.
SQUAD = [
    ("Keeper One", "GK", "ZKA", 5.0),
    ("Keeper Two", "GK", "ZKB", 4.0),
    ("Back One", "DEF", "ZKC", 6.0),
    ("Back Two", "DEF", "ZKC", 5.0),
    ("Back Three", "DEF", "ZKD", 4.5),
    ("Back Four", "DEF", "ZKE", 4.0),
    ("Back Five", "DEF", "ZKF", 4.0),
    ("Mid One", "MID", "ZKC", 9.0),
    ("Mid Two", "MID", "ZKD", 7.0),
    ("Mid Three", "MID", "ZKE", 6.0),
    ("Mid Four", "MID", "ZKF", 5.0),
    ("Mid Five", "MID", "ZKG", 4.5),
    ("Front One", "FWD", "ZKA", 11.0),
    ("Front Two", "FWD", "ZKB", 7.0),
    ("Front Three", "FWD", "ZKG", 5.5),
]
SQUAD_TOTAL = sum(price for _, _, _, price in SQUAD)
BANK = 1.0

# Pool players to buy. Prices chosen against a 7.0 "Mid Two" + 1.0 bank = 8.0
# budget, so affordable and unaffordable are both one clear step away.
POOL = [
    (POOL_ID_BASE + 1, "Cheap Mid", "MID", "ZKH", 6.0),
    (POOL_ID_BASE + 2, "Dear Mid", "MID", "ZKH", 12.0),
    (POOL_ID_BASE + 3, "Cheap Fwd", "FWD", "ZKH", 6.0),
    (POOL_ID_BASE + 4, "Club Full Mid", "MID", "ZKC", 6.0),
    (POOL_ID_BASE + 5, "Club Full Def", "DEF", "ZKC", 5.0),
    (POOL_ID_BASE + 6, "Spare Def", "DEF", "ZKH", 5.0),
]
POOL_IDS = tuple(row[0] for row in POOL)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute(
            "DELETE FROM fpl_players WHERE id >= %s AND id < %s",
            (POOL_ID_BASE, POOL_ID_BASE + 1000),
        )


@pytest.fixture
def squad():
    """A legal squad with a known bank, known team value, and a known
    transfer history."""
    _cleanup()
    with _conn() as conn:
        # team_value is squad selling total PLUS bank (issue 20's lesson), and
        # is set so selling prices come out EXACT -- affordability arithmetic
        # is then unambiguous and the tests measure the rule, not the caveat.
        conn.execute(
            "INSERT INTO teams (id, name, bank, team_value) VALUES (%s, %s, %s, %s)",
            (TEST_TEAM_ID, "Rules Test", BANK, round(SQUAD_TOTAL + BANK, 1)),
        )
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, PLANNING_GW),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO players (team_id, name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(TEST_TEAM_ID, *row) for row in SQUAD],
            )
            cur.executemany(
                "INSERT INTO fpl_players (id, name, full_name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [(pid, name, name, pos, club, price) for pid, name, pos, club, price in POOL],
            )
            # The squad players also need pool rows: get_player looks players
            # up in fpl_players, not in the squad table.
            cur.executemany(
                "INSERT INTO fpl_players (id, name, full_name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (POOL_ID_BASE + 100 + i, name, name, pos, club, price)
                    for i, (name, pos, club, price) in enumerate(SQUAD)
                ],
            )
            # One transfer made in GW48, none in GW49: 1 free at GW48, spend
            # it, 1 accrues for GW49, unspent, so 2 going into GW50.
            cur.executemany(
                "INSERT INTO entry_gameweek_history (team_id, gameweek, event_transfers) "
                "VALUES (%s, %s, %s)",
                [(TEST_TEAM_ID, 48, 1), (TEST_TEAM_ID, 49, 0)],
            )
        conn.commit()

        yield conn

    with _conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute(
            "DELETE FROM fpl_players WHERE id >= %s AND id < %s",
            (POOL_ID_BASE, POOL_ID_BASE + 1000),
        )


def _rules(verdict) -> list[str]:
    return [v.rule for v in verdict.violations]


# --- the rule the codebase actually got wrong --------------------------------


def test_a_position_mismatched_transfer_is_refused_naming_both_positions(squad):
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Fwd", TEST_TEAM_ID)

    assert verdict.legal is False
    assert "position_mismatch" in _rules(verdict)
    message = next(v.message for v in verdict.violations if v.rule == "position_mismatch")
    assert "MID" in message and "FWD" in message
    assert "Mid Two" in message and "Cheap Fwd" in message


def test_an_illegal_move_never_returns_a_comparison_or_a_gain_signal(squad):
    """AC: recommend_transfers validates BEFORE comparing. A gain_signal on an
    impossible move is misinformation regardless of the prose accompanying it
    -- it ranks a move FPL will not accept against ones it will."""
    result = recommend_transfers(squad, "Mid Two", "Cheap Fwd", False, TEST_TEAM_ID)

    assert isinstance(result, SquadMoveVerdict)
    assert not isinstance(result, TransferComparison)
    assert not hasattr(result, "gain_signal")
    assert "position_mismatch" in _rules(result)


def test_a_legal_move_still_returns_a_full_comparison(squad):
    result = recommend_transfers(squad, "Mid Two", "Cheap Mid", False, TEST_TEAM_ID)

    assert isinstance(result, TransferComparison)
    assert result.player_in.player.name == "Cheap Mid"
    assert result.verdict is not None and result.verdict.legal is True


# --- the rest of the rules ---------------------------------------------------


def test_a_move_breaching_the_three_per_club_limit_is_refused_naming_the_players(squad):
    verdict = validate_squad_move(squad, "Mid Two", "Club Full Mid", TEST_TEAM_ID)

    assert verdict.legal is False
    message = next(v.message for v in verdict.violations if v.rule == "three_per_club")
    assert "ZKC" in message
    for held in ("Back One", "Back Two", "Mid One"):
        assert held in message


def test_selling_a_player_from_the_full_club_frees_the_slot(squad):
    """Counted AFTER the move. Checking before would refuse the commonest
    legal way to get a fourth-ranked asset from a club you already hold."""
    verdict = validate_squad_move(squad, "Mid One", "Club Full Mid", TEST_TEAM_ID)

    assert "three_per_club" not in _rules(verdict)
    assert verdict.legal is True


def test_an_unaffordable_move_is_refused_and_reports_the_shortfall(squad):
    """Budget is the outgoing player's selling price plus the bank: 7.0 + 1.0."""
    verdict = validate_squad_move(squad, "Mid Two", "Dear Mid", TEST_TEAM_ID)

    assert verdict.legal is False
    assert "affordability" in _rules(verdict)
    assert verdict.budget == 8.0
    assert verdict.price_in == 12.0
    assert verdict.shortfall == 4.0
    assert "4.0" in next(v.message for v in verdict.violations if v.rule == "affordability")


def test_the_budget_is_the_outgoing_price_plus_the_bank(squad):
    """Mid Five sells for 4.5 and the bank holds 1.0, so the budget is 5.5 --
    not 4.5, and not the whole squad value. Getting this wrong in either
    direction refuses legal moves or approves impossible ones."""
    verdict = validate_squad_move(squad, "Mid Five", "Cheap Mid", TEST_TEAM_ID)

    assert verdict.budget == 5.5
    assert verdict.legal is False  # Cheap Mid at 6.0 is 0.5 beyond it
    assert verdict.shortfall == 0.5


def test_bringing_in_a_player_already_owned_is_refused(squad):
    verdict = validate_squad_move(squad, "Mid Two", "Mid One", TEST_TEAM_ID)

    assert verdict.legal is False
    assert "already_owned" in _rules(verdict)


def test_selling_a_player_not_in_the_squad_is_refused(squad):
    verdict = validate_squad_move(squad, "Somebody Else", "Cheap Mid", TEST_TEAM_ID)

    assert verdict.legal is False
    assert "not_in_squad" in _rules(verdict)


def test_a_move_leaving_the_squad_outside_the_required_shape_is_refused(squad):
    """The shape rule fires on its own when nothing else explains it: a squad
    ingested already out of shape (6 DEF, 4 MID) stays out of shape after a
    perfectly like-for-like defender swap."""
    squad.execute(
        "DELETE FROM players WHERE team_id = %s AND name = %s", (TEST_TEAM_ID, "Mid Five")
    )
    squad.execute(
        "INSERT INTO players (team_id, name, position, club, price) VALUES (%s,%s,%s,%s,%s)",
        (TEST_TEAM_ID, "Back Six", "DEF", "ZKG", 4.0),
    )
    squad.commit()

    # A ZKH defender, not a ZKC one: ZKC already supplies three (Back One,
    # Back Two, Mid One), so buying from there would add a club-limit
    # violation and this test would stop measuring the shape rule.
    verdict = validate_squad_move(squad, "Back Six", "Spare Def", TEST_TEAM_ID)

    assert verdict.legal is False
    assert _rules(verdict) == ["squad_composition"]
    message = next(v.message for v in verdict.violations if v.rule == "squad_composition")
    assert "6 DEF" in message and "4 MID" in message


def test_composition_is_not_reported_twice_when_a_mismatch_already_explains_it(squad):
    """A position mismatch ALWAYS breaks the shape, so reporting both states
    the same fact twice and buries the reason that matters."""
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Fwd", TEST_TEAM_ID)

    assert "position_mismatch" in _rules(verdict)
    assert "squad_composition" not in _rules(verdict)


def test_a_verdict_names_the_violation_rather_than_returning_a_bare_boolean(squad):
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Fwd", TEST_TEAM_ID)

    assert verdict.summary.startswith("REFUSED")
    assert all(v.message and v.rule for v in verdict.violations)


def test_all_violations_are_collected_not_short_circuited(squad):
    """One round trip should surface everything wrong with a move."""
    verdict = validate_squad_move(squad, "Back Three", "Dear Mid", TEST_TEAM_ID)

    assert {"position_mismatch", "affordability"} <= set(_rules(verdict))


def test_an_unknown_incoming_player_is_refused_rather_than_guessed_at(squad):
    verdict = validate_squad_move(squad, "Mid Two", "Nobody At All", TEST_TEAM_ID)

    assert verdict.legal is False
    assert "unknown_player" in _rules(verdict)


def test_a_legal_verdict_does_not_argue_the_move_is_good(squad):
    """Refusal and pushback are different things and the output must not blur
    them: a legality pass is not an endorsement."""
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Mid", TEST_TEAM_ID)

    assert verdict.legal is True
    assert verdict.violations == []
    assert "not an argument that the move is a good one" in verdict.summary


# --- free transfers, derived rather than assumed -----------------------------


def test_free_transfers_are_derived_from_ingested_history(squad):
    """AC: whether the transfer costs a hit comes from ingested
    event_transfers and rolled free transfers, not from the user."""
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Mid", TEST_TEAM_ID)

    # GW48: 1 free, 1 spent -> 0 left, +1 for GW49 = 1. GW49: 0 spent -> 1
    # left, +1 for GW50 = 2.
    assert verdict.free_transfers == 2
    assert verdict.is_hit is False
    assert verdict.hit_cost == 0


def test_transfers_made_in_the_planning_gameweek_are_subtracted():
    """The step that makes the number useful. Two accrued, two already spent
    this week, none left."""
    history = [
        {"gameweek": 1, "event_transfers": 0},
        {"gameweek": 2, "event_transfers": 2},
    ]
    assert free_transfers_at(history, {}, 2) == 0


def test_free_transfers_roll_up_but_are_capped():
    history = [{"gameweek": gw, "event_transfers": 0} for gw in range(1, 12)]
    assert free_transfers_at(history, {}, 12) == 5


def test_a_wildcard_gameweek_does_not_consume_the_free_transfer():
    """FPL retains saved free transfers across a wildcard or free hit, so a
    ten-transfer wildcard week must not read as ten spent."""
    history = [
        {"gameweek": 1, "event_transfers": 0},
        {"gameweek": 2, "event_transfers": 10},
    ]
    assert free_transfers_at(history, {2: "wildcard"}, 3) == 3
    # Without the chip the same week would exhaust everything.
    assert free_transfers_at(history, {}, 3) == 1


def test_spending_more_than_you_have_does_not_go_negative():
    history = [{"gameweek": 1, "event_transfers": 5}]
    assert free_transfers_at(history, {}, 2) == 1


def test_accrual_alone_can_never_reach_zero(squad):
    """FPL grants a free transfer every gameweek however many were spent
    before, so a hit test built on the accrual alone would always answer no.
    Two GW48 transfers against one free one still leaves one for GW49."""
    squad.execute(
        "UPDATE entry_gameweek_history SET event_transfers = 4 WHERE team_id = %s",
        (TEST_TEAM_ID,),
    )
    squad.commit()

    verdict = validate_squad_move(squad, "Mid Two", "Cheap Mid", TEST_TEAM_ID)

    assert verdict.free_transfers == 1
    assert verdict.is_hit is False
    # And the figure is flagged as an upper bound, because this gameweek's
    # own transfers are not published until its deadline passes.
    assert "upper bound" in verdict.note


def test_a_transfer_beyond_the_free_ones_is_reported_as_a_hit(squad):
    """Once the planning gameweek's own transfer count is known -- its
    deadline has passed -- the count becomes exact and can reach zero."""
    squad.execute(
        "INSERT INTO entry_gameweek_history (team_id, gameweek, event_transfers) "
        "VALUES (%s, %s, %s)",
        (TEST_TEAM_ID, PLANNING_GW, 2),
    )
    squad.commit()

    verdict = validate_squad_move(squad, "Mid Two", "Cheap Mid", TEST_TEAM_ID)

    assert verdict.free_transfers == 0
    assert verdict.is_hit is True
    assert verdict.hit_cost == 4
    assert "upper bound" not in (verdict.note or "")


def test_a_derived_hit_overrides_the_callers_free_transfer_claim(squad):
    """"Assume it's a free transfer" is the commonest thing a user says and
    the commonest thing that is wrong. Free-transfer state is ingested now."""
    squad.execute(
        "INSERT INTO entry_gameweek_history (team_id, gameweek, event_transfers) "
        "VALUES (%s, %s, %s)",
        (TEST_TEAM_ID, PLANNING_GW, 2),
    )
    squad.commit()

    result = recommend_transfers(squad, "Mid Two", "Cheap Mid", False, TEST_TEAM_ID)

    assert isinstance(result, TransferComparison)
    assert result.is_hit is True
    assert result.hit_cost == 4
    assert result.net_gain_signal == result.gain_signal - 4


# --- the finalisation gate ---------------------------------------------------


def test_validate_incoming_refuses_a_player_with_no_position_to_sell(squad):
    """submit_recommendation names only the player coming IN, so the gate is
    a possibility check: is there ANY legal version of this move?"""
    squad.execute(
        "DELETE FROM players WHERE team_id = %s AND position = 'FWD'", (TEST_TEAM_ID,)
    )
    squad.commit()

    problems = validate_incoming(squad, "Cheap Fwd", TEST_TEAM_ID)

    assert problems
    assert "like-for-like" in " ".join(problems)


def test_validate_incoming_refuses_a_player_nobody_could_afford(squad):
    """Selling the most expensive player of that position plus the bank is
    the BEST case. Failing it means no legal version exists."""
    problems = validate_incoming(squad, "Dear Mid", TEST_TEAM_ID)

    assert problems
    assert "no version of this transfer is affordable" in " ".join(problems)


def test_validate_incoming_refuses_a_player_already_owned(squad):
    problems = validate_incoming(squad, "Mid One", TEST_TEAM_ID)

    assert problems
    assert "already in the squad" in " ".join(problems)


def test_validate_incoming_allows_a_move_some_pairing_could_make_legal(squad):
    """Deliberately permissive: the model cannot tell the gate which pairing
    it meant, and a false refusal would teach it to route around the check.
    recommend_transfers is where a named pairing is fully validated."""
    assert validate_incoming(squad, "Cheap Mid", TEST_TEAM_ID) == []


def test_validate_incoming_refuses_a_club_slot_no_legal_sale_could_free(squad):
    """Three ZKC players, none of them a DEF, so no like-for-like sale can
    make room for a ZKC defender -- refusable without knowing who goes out."""
    squad.execute(
        "UPDATE players SET position = 'MID' WHERE team_id = %s AND name IN "
        "('Back One', 'Back Two')",
        (TEST_TEAM_ID,),
    )
    squad.commit()

    problems = validate_incoming(squad, "Club Full Def", TEST_TEAM_ID)

    assert "no like-for-like sale" in " ".join(problems)


def test_validate_incoming_allows_a_full_club_when_a_legal_sale_exists(squad):
    """ZKC holds a defender, so selling that defender makes room. Refusing
    here would block a legal move the model had every right to propose."""
    assert validate_incoming(squad, "Club Full Def", TEST_TEAM_ID) == []


def test_the_squad_fixture_is_a_legal_fifteen(squad):
    """Guards the fixture itself: if the seeded squad were illegal, every
    composition assertion above would be measuring the seeding."""
    assert len(SQUAD) == SQUAD_SIZE
    verdict = validate_squad_move(squad, "Mid Two", "Cheap Mid", TEST_TEAM_ID)
    assert "squad_composition" not in _rules(verdict)
