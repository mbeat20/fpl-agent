"""Issue 25: the squad rules applied over time.

Pure tests, no database. `walk_plan` takes dicts and returns dicts for the
same reason `squad_rules` does: the arithmetic is the deliverable, and it
should be assertable without seeding a squad to get at it.

The failure these guard against is the one ticket 12 shipped and nobody
checked: a plan's third step is legal or not depending on what its first two
did, and until now the model did that arithmetic in prose across five steps
with nothing verifying it.
"""

from fpl_coach.plan_rules import (
    LEGALITY_RULES,
    PlannedStep,
    is_legal,
    walk_plan,
)
from fpl_coach.squad_rules import MAX_FREE_TRANSFERS

# A legal 15: 2 GK, 5 DEF, 5 MID, 3 FWD. "ZKC" supplies exactly three, so the
# club limit is one move away from being breached.
SQUAD = [
    {"name": "Keeper One", "position": "GK", "club": "ZKA", "price": 5.0},
    {"name": "Keeper Two", "position": "GK", "club": "ZKB", "price": 4.0},
    {"name": "Back One", "position": "DEF", "club": "ZKC", "price": 6.0},
    {"name": "Back Two", "position": "DEF", "club": "ZKC", "price": 5.0},
    {"name": "Back Three", "position": "DEF", "club": "ZKD", "price": 4.5},
    {"name": "Back Four", "position": "DEF", "club": "ZKE", "price": 4.0},
    {"name": "Back Five", "position": "DEF", "club": "ZKF", "price": 4.0},
    {"name": "Mid One", "position": "MID", "club": "ZKC", "price": 9.0},
    {"name": "Mid Two", "position": "MID", "club": "ZKD", "price": 7.0},
    {"name": "Mid Three", "position": "MID", "club": "ZKE", "price": 6.0},
    {"name": "Mid Four", "position": "MID", "club": "ZKF", "price": 5.0},
    {"name": "Mid Five", "position": "MID", "club": "ZKG", "price": 4.5},
    {"name": "Front One", "position": "FWD", "club": "ZKA", "price": 11.0},
    {"name": "Front Two", "position": "FWD", "club": "ZKB", "price": 7.0},
    {"name": "Front Three", "position": "FWD", "club": "ZKG", "price": 5.5},
]

START_GW = 50


def _held(name: str) -> dict:
    return next(dict(p) for p in SQUAD if p["name"] == name)


def _pool(name, position="MID", club="ZKH", price=6.0) -> dict:
    return {"name": name, "position": position, "club": club, "price": price}


def _transfer(index, gameweek, player_in, player_out) -> PlannedStep:
    return PlannedStep(
        index=index,
        gameweek=gameweek,
        decision="transfer",
        player_in=player_in,
        player_out=player_out,
    )


def _walk(steps, bank=1.0, free=1, blanks=None, exact=True):
    return walk_plan(
        steps,
        squad=SQUAD,
        bank=bank,
        free_at_start=free,
        start_gameweek=START_GW,
        blanks=blanks,
        selling_prices_exact=exact,
    )


def _rules(violations) -> list[str]:
    return [v.rule for _, v in violations]


# --- the thing ticket 12 could not do ---------------------------------------


def test_step_three_is_priced_against_what_steps_one_and_two_left_behind():
    """The whole reason this module exists. Each step sells a 7.0 and buys a
    dearer player; by step 3 the bank is exhausted, and nothing before this
    ticket would have noticed."""
    steps = [
        _transfer(0, START_GW, _pool("Buy One", price=8.0), _held("Mid Two")),
        _transfer(1, START_GW + 1, _pool("Buy Two", price=7.0), _held("Mid Three")),
        _transfer(2, START_GW + 2, _pool("Buy Three", price=9.0), _held("Mid Four")),
    ]

    violations, costs, totals = _walk(steps, bank=1.0)

    # 1.0 + 7.0 - 8.0 = 0.0; 0.0 + 6.0 - 7.0 = -1.0 -- already short.
    assert costs[0]["bank_after"] == 0.0
    assert costs[1]["bank_before"] == 0.0
    assert "affordability" in _rules(violations)
    assert is_legal(violations) is False


def test_a_plan_whose_arithmetic_holds_is_accepted():
    steps = [
        _transfer(0, START_GW, _pool("Cheap Mid", price=6.0), _held("Mid Two")),
        _transfer(1, START_GW + 2, _pool("Cheap Def", position="DEF", price=4.0),
                  _held("Back Three")),
    ]

    violations, costs, totals = _walk(steps, bank=1.0, free=1)

    assert violations == []
    assert is_legal(violations) is True
    assert totals["total_hit_cost"] == 0


def test_selling_a_player_frees_the_club_slot_for_a_later_step():
    """Counted against the squad the earlier step PRODUCED. Checking against
    today's squad would refuse the commonest legal way to get a fourth asset
    from a club you are already three deep in."""
    steps = [
        # Sell a ZKC player first...
        _transfer(0, START_GW, _pool("Spare Def", position="DEF", price=5.0),
                  _held("Back Two")),
        # ...which makes room for a ZKC player next week.
        _transfer(1, START_GW + 1, _pool("New Def", position="DEF", club="ZKC", price=5.0),
                  _held("Back Four")),
    ]

    violations, _, _ = _walk(steps, bank=2.0, free=1)

    assert "three_per_club" not in _rules(violations)


def test_buying_a_fourth_from_a_club_is_still_refused_when_no_slot_is_freed():
    steps = [
        _transfer(0, START_GW, _pool("Cheap Mid", price=6.0), _held("Mid Two")),
        _transfer(1, START_GW + 1, _pool("New Def", position="DEF", club="ZKC", price=5.0),
                  _held("Back Four")),
    ]

    violations, _, _ = _walk(steps, bank=2.0)

    assert "three_per_club" in _rules(violations)


def test_selling_the_same_player_twice_is_caught():
    """Only checkable against a running squad: both steps name a player who is
    in the squad TODAY, and the second one is not in the squad by the time it
    runs."""
    steps = [
        _transfer(0, START_GW, _pool("Buy One", price=6.0), _held("Mid Two")),
        _transfer(1, START_GW + 1, _pool("Buy Two", price=6.0), _held("Mid Two")),
    ]

    violations, _, _ = _walk(steps, bank=2.0)

    assert "not_in_squad" in _rules(violations)


def test_buying_a_player_an_earlier_step_already_bought_is_caught():
    same = _pool("Buy One", price=6.0)
    steps = [
        _transfer(0, START_GW, dict(same), _held("Mid Two")),
        _transfer(1, START_GW + 1, dict(same), _held("Mid Three")),
    ]

    violations, _, _ = _walk(steps, bank=2.0)

    assert "already_owned" in _rules(violations)


# --- free transfers and hits ------------------------------------------------


def test_two_transfers_in_one_gameweek_costs_a_hit():
    """Steps legitimately share a gameweek (ticket 12 hit this and loosened a
    test for it). The second one is the hit."""
    steps = [
        _transfer(0, START_GW, _pool("Buy One", price=5.0), _held("Mid Two")),
        _transfer(1, START_GW, _pool("Buy Two", price=4.0), _held("Mid Three")),
    ]

    violations, costs, totals = _walk(steps, bank=5.0, free=1)

    assert costs[0]["is_hit"] is False
    assert costs[1]["is_hit"] is True
    assert totals["total_hit_cost"] == 4


def test_free_transfers_accrue_across_the_gaps_between_steps():
    """A gameweek with no step still earns its free transfer, so a plan that
    spaces its moves out takes no hits."""
    steps = [
        _transfer(0, START_GW, _pool("Buy One", price=5.0), _held("Mid Two")),
        _transfer(1, START_GW + 3, _pool("Buy Two", price=4.0), _held("Mid Three")),
        _transfer(2, START_GW + 3, _pool("Buy Three", price=4.0), _held("Mid Four")),
    ]

    violations, costs, totals = _walk(steps, bank=10.0, free=1)

    # 1 free at GW50, spent. Three weeks pass -> 3 accrued, capped well under
    # the limit, so both GW53 moves are free.
    assert costs[1]["free_transfers_before"] == 3
    assert totals["total_hit_cost"] == 0


def test_free_transfers_stop_accruing_at_the_cap():
    steps = [
        _transfer(0, START_GW + 20, _pool("Buy One", price=5.0), _held("Mid Two")),
    ]

    _, costs, _ = _walk(steps, bank=5.0, free=1)

    assert costs[0]["free_transfers_before"] == MAX_FREE_TRANSFERS


def test_hits_are_totalled_across_the_whole_plan():
    """A plan whose steps are each individually defensible can still cost 12
    points that nobody added up."""
    steps = [
        _transfer(i, START_GW, _pool(f"Buy {i}", price=4.0), _held(name))
        for i, name in enumerate(["Mid Two", "Mid Three", "Mid Four", "Mid Five"])
    ]

    _, _, totals = _walk(steps, bank=20.0, free=1)

    assert totals["total_hit_cost"] == 12  # three of the four are hits


# --- ordering ---------------------------------------------------------------


def test_steps_out_of_gameweek_order_are_walked_in_gameweek_order():
    """Ticket 12 established that nothing requires steps in order. Walking
    them as submitted would price the GW52 move against a bank the GW50 move
    had not yet touched."""
    steps = [
        _transfer(0, START_GW + 2, _pool("Later Buy", price=9.0), _held("Mid Three")),
        _transfer(1, START_GW, _pool("Earlier Buy", price=5.0), _held("Mid Two")),
    ]

    _, costs, _ = _walk(steps, bank=1.0, free=1)

    by_index = {cost["step_index"]: cost for cost in costs}
    # The GW50 step runs first and banks 7.0 - 5.0 = 2.0 on top of 1.0.
    assert by_index[1]["bank_before"] == 1.0
    assert by_index[1]["bank_after"] == 3.0
    assert by_index[0]["bank_before"] == 3.0


def test_violations_are_reported_against_the_submitted_step_index():
    """The model has to be able to find the step it wrote. Reporting against
    a sorted position would send it to the wrong one."""
    steps = [
        _transfer(0, START_GW + 1, _pool("Bad Buy", position="FWD", price=5.0),
                  _held("Mid Two")),
        _transfer(1, START_GW, _pool("Fine Buy", price=5.0), _held("Mid Three")),
    ]

    violations, _, _ = _walk(steps, bank=5.0)

    offending = [step.index for step, v in violations if v.rule == "position_mismatch"]
    assert offending == [0]


# --- refusals that make the plan checkable ----------------------------------


def test_a_transfer_step_that_does_not_say_who_is_sold_is_refused():
    """The refusal that makes everything above possible. Without the outgoing
    player the budget for every later step is unknowable, so accepting it
    would mean silently not checking the rest of the plan."""
    steps = [
        PlannedStep(
            index=0,
            gameweek=START_GW,
            decision="transfer",
            player_in=_pool("Buy One"),
            player_out=None,
        )
    ]

    violations, _, _ = _walk(steps)

    assert _rules(violations) == ["missing_player_out"]
    assert is_legal(violations) is False
    assert "will not be accepted" in violations[0][1].message


def test_a_step_in_a_gameweek_that_has_already_gone_is_refused():
    steps = [_transfer(0, START_GW - 1, _pool("Buy One", price=5.0), _held("Mid Two"))]

    violations, _, _ = _walk(steps)

    assert "past_gameweek" in _rules(violations)


# --- blanks: reported, but not an FPL illegality -----------------------------


def test_a_step_aimed_at_a_blank_gameweek_is_reported():
    steps = [
        _transfer(0, START_GW, _pool("Blank Buy", club="ZKZ", price=5.0), _held("Mid Two"))
    ]

    violations, _, _ = _walk(steps, bank=5.0, blanks={"ZKZ": {START_GW}})

    assert "blank_gameweek" in _rules(violations)


def test_a_blank_is_a_violation_but_not_an_illegality():
    """Ticket 22's distinction, preserved. Captaining a player with no fixture
    is perfectly legal and merely guarantees zero -- so it must be reported
    and revised, without claiming FPL would refuse it."""
    steps = [
        PlannedStep(
            index=0,
            gameweek=START_GW,
            decision="captain",
            player_in=_pool("Blank Captain", club="ZKZ", price=5.0),
        ),
        PlannedStep(index=1, gameweek=START_GW + 1, decision="roll"),
    ]

    violations, _, _ = _walk(steps, blanks={"ZKZ": {START_GW}})

    assert _rules(violations) == ["blank_gameweek"]
    assert is_legal(violations) is True
    assert "blank_gameweek" not in LEGALITY_RULES


# --- degradation ------------------------------------------------------------


def test_no_bank_means_affordability_is_not_checked_rather_than_assumed():
    steps = [_transfer(0, START_GW, _pool("Dear Buy", price=99.0), _held("Mid Two"))]

    violations, costs, _ = _walk(steps, bank=None)

    assert "affordability" not in _rules(violations)
    assert costs[0]["bank_after"] is None


def test_no_free_transfer_history_means_hits_are_not_counted():
    steps = [
        _transfer(0, START_GW, _pool("Buy One", price=5.0), _held("Mid Two")),
        _transfer(1, START_GW, _pool("Buy Two", price=4.0), _held("Mid Three")),
    ]

    _, costs, totals = _walk(steps, bank=10.0, free=None)

    assert totals["total_hit_cost"] == 0
    assert all(cost["is_hit"] is False for cost in costs)
    assert all(cost["free_transfers_before"] is None for cost in costs)


def test_every_step_is_reported_not_just_the_first_broken_one():
    """Stopping at the first bad step would report one problem per round trip
    and hide everything downstream, which is how a model ends up playing
    twenty questions with a validator."""
    steps = [
        _transfer(0, START_GW, _pool("Bad One", position="FWD", price=5.0), _held("Mid Two")),
        _transfer(1, START_GW + 1, _pool("Bad Two", position="GK", price=5.0),
                  _held("Mid Three")),
    ]

    violations, _, _ = _walk(steps, bank=5.0)

    assert _rules(violations).count("position_mismatch") == 2
