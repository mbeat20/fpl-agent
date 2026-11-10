"""Issue 25: a Roadmap is a plan the user is following.

`test_plan_rules.py` covers the arithmetic without a database. This covers
what only a database can answer: that a plan's names resolve to real players
and prices, that versions accumulate instead of overwriting, and that the
check-in notices the world moving under a stored plan.

Everything is seeded on team 90224 and gameweeks 60-64, far outside anything
real. `fixtures` and `fpl_players` are global tables that `run_ingestion`
replaces wholesale, so nothing here may depend on what they happen to hold --
and the ids are chosen so nothing here can be mistaken for real data either.
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.models import Evidence, PlanStep, RecommendationPick, TransferPlan
from fpl_coach.roadmap import (
    check_in,
    get_live_roadmap,
    get_roadmap_versions,
    plan_problems,
    save_roadmap,
    validate_plan,
)

TEST_TEAM_ID = 90224
POOL_ID_BASE = 977_000
FIXTURE_ID_BASE = 993_000
START_GW = 60
CLUB_IDS = (9921, 9922, 9923, 9924, 9925, 9926, 9927)
# Five clubs of three for the squad -- FPL allows at most three from any one,
# so a 15 spread over fewer would be illegal before any plan touched it, and
# every step would then fail for a reason that has nothing to do with the step.
HOME, AWAY, THIRD, FOURTH, FIFTH = "ZRA", "ZRB", "ZRC", "ZRE", "ZRF"
# The clubs the POOL plays for. TARGET holds no squad player, so buying from
# it never brushes the club limit; BLANKING never plays at all.
TARGET, BLANKING = "ZRG", "ZRD"

SQUAD = [
    ("Keeper One", "GK", HOME, 5.0),
    ("Back Three", "DEF", HOME, 4.5),
    ("Mid Two", "MID", HOME, 7.0),
    ("Keeper Two", "GK", AWAY, 4.0),
    ("Back Four", "DEF", AWAY, 4.0),
    ("Mid Three", "MID", AWAY, 6.0),
    ("Back One", "DEF", THIRD, 6.0),
    ("Mid One", "MID", THIRD, 9.0),
    ("Front Three", "FWD", THIRD, 5.5),
    ("Back Two", "DEF", FOURTH, 5.0),
    ("Mid Four", "MID", FOURTH, 5.0),
    ("Front One", "FWD", FOURTH, 11.0),
    ("Back Five", "DEF", FIFTH, 4.0),
    ("Mid Five", "MID", FIFTH, 4.5),
    ("Front Two", "FWD", FIFTH, 7.0),
]
SQUAD_TOTAL = sum(price for _, _, _, price in SQUAD)
BANK = 1.0

POOL = [
    (POOL_ID_BASE + 1, "Cheap Mid", "MID", TARGET, 6.0, "a"),
    (POOL_ID_BASE + 2, "Dear Mid", "MID", TARGET, 12.0, "a"),
    (POOL_ID_BASE + 3, "Other Mid", "MID", TARGET, 6.5, "a"),
    (POOL_ID_BASE + 4, "Hurt Mid", "MID", TARGET, 6.0, "i"),
    (POOL_ID_BASE + 5, "Blank Mid", "MID", BLANKING, 6.0, "a"),
]


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("DELETE FROM fixtures WHERE id >= %s", (FIXTURE_ID_BASE,))
        conn.execute("DELETE FROM clubs WHERE id = ANY(%s)", (list(CLUB_IDS),))
        conn.execute(
            "DELETE FROM fpl_players WHERE id >= %s AND id < %s",
            (POOL_ID_BASE, POOL_ID_BASE + 1000),
        )


@pytest.fixture
def seeded():
    """A legal 15, a known bank, a fixture calendar, and a transfer history.

    ZRG (every buyable pool player's club) and ZRA play each other through
    GW60-64. ZRD -- the club "Blank Mid" plays for -- never plays at all, so a
    step aimed at them is a blank.
    """
    _cleanup()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO teams (id, name, bank, team_value) VALUES (%s, %s, %s, %s)",
            (TEST_TEAM_ID, "Roadmap Test", BANK, round(SQUAD_TOTAL + BANK, 1)),
        )
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, START_GW),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clubs (id, name, short_name) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET short_name = EXCLUDED.short_name",
                [
                    (9921, "Zed Red", HOME),
                    (9922, "Zed Blue", AWAY),
                    (9923, "Zed Green", THIRD),
                    (9924, "Zed Dark", BLANKING),
                    (9925, "Zed Amber", FOURTH),
                    (9926, "Zed Grey", FIFTH),
                    (9927, "Zed Gold", TARGET),
                ],
            )
            cur.executemany(
                "INSERT INTO players (team_id, name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(TEST_TEAM_ID, *row) for row in SQUAD],
            )
            cur.executemany(
                "INSERT INTO fpl_players (id, name, full_name, position, club, price, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (pid, name, name, position, club, price, status)
                    for pid, name, position, club, price, status in POOL
                ],
            )
            # Squad players need pool rows too: a plan step names them and
            # find_player looks in fpl_players, not in the squad table.
            cur.executemany(
                "INSERT INTO fpl_players (id, name, full_name, position, club, price, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'a') ON CONFLICT DO NOTHING",
                [
                    (POOL_ID_BASE + 100 + i, name, name, pos, club, price)
                    for i, (name, pos, club, price) in enumerate(SQUAD)
                ],
            )
            cur.executemany(
                "INSERT INTO fixtures (id, gameweek, home_team, away_team, "
                "home_difficulty, away_difficulty) VALUES (%s, %s, %s, %s, 3, 3)",
                [(FIXTURE_ID_BASE + n, START_GW + n, TARGET, HOME) for n in range(5)]
                + [
                    (FIXTURE_ID_BASE + 100 + n, START_GW + n, AWAY, THIRD)
                    for n in range(5)
                ],
            )
            # No transfers made in GW59, so one free transfer carries into 60.
            cur.execute(
                "INSERT INTO entry_gameweek_history (team_id, gameweek, event_transfers) "
                "VALUES (%s, %s, %s)",
                (TEST_TEAM_ID, START_GW - 1, 0),
            )
        conn.commit()
        yield conn

    _cleanup()


def _step(gameweek, player_in=None, player_out=None, decision="transfer") -> PlanStep:
    return PlanStep(
        gameweek=gameweek,
        decision=decision,
        pick=RecommendationPick(player_id=1, player_name=player_in) if player_in else None,
        player_out=(
            RecommendationPick(player_id=2, player_name=player_out) if player_out else None
        ),
        evidence=[Evidence(text="because")],
        risks=["a risk"],
    )


def _plan(*steps, horizon="multi-week") -> TransferPlan:
    return TransferPlan(horizon=horizon, confidence=0.7, steps=list(steps))


def _legal_plan() -> TransferPlan:
    return _plan(
        _step(START_GW, "Cheap Mid", "Mid Two"),
        _step(START_GW + 2, "Other Mid", "Mid Three"),
    )


# --- validation against the real squad ---------------------------------------


def test_a_legal_plan_validates(seeded):
    validation = validate_plan(seeded, _legal_plan(), TEST_TEAM_ID)

    assert validation.legal is True
    assert validation.violations == []
    assert validation.transfers_planned == 2
    assert "legal and its arithmetic holds" in validation.summary


def test_cumulative_affordability_is_checked_against_real_prices(seeded):
    """Sell a 7.0 for a 12.0 with 1.0 banked and the first step is already
    short; the second then has nothing to work with."""
    plan = _plan(
        _step(START_GW, "Dear Mid", "Mid Two"),
        _step(START_GW + 1, "Cheap Mid", "Mid Three"),
    )

    validation = validate_plan(seeded, plan, TEST_TEAM_ID)

    assert validation.legal is False
    assert any(v.rule == "affordability" for v in validation.violations)
    assert validation.violations[0].step_index == 0


def test_a_step_aimed_at_a_blank_gameweek_is_caught_from_real_fixtures(seeded):
    """ZRD has no fixtures at all, so every gameweek is a blank for them."""
    plan = _plan(
        _step(START_GW, "Blank Mid", "Mid Two"),
        _step(START_GW + 2, "Cheap Mid", "Mid Three"),
    )

    validation = validate_plan(seeded, plan, TEST_TEAM_ID)

    assert any(v.rule == "blank_gameweek" for v in validation.violations)
    # Legal, and still refused -- ticket 22's distinction survives.
    assert validation.legal is True
    assert plan_problems(seeded, plan, TEST_TEAM_ID) != []


def test_plan_problems_names_the_step_the_model_wrote(seeded):
    plan = _plan(
        _step(START_GW, "Cheap Mid", "Mid Two"),
        _step(START_GW + 1, "Dear Mid", "Mid Three"),
    )

    problems = plan_problems(seeded, plan, TEST_TEAM_ID)

    assert any(p.startswith(f"Step 2 (GW{START_GW + 1})") for p in problems)


def test_a_transfer_step_with_no_outgoing_player_is_refused(seeded):
    plan = _plan(
        _step(START_GW, "Cheap Mid"),
        _step(START_GW + 1, None, None, decision="roll"),
    )

    problems = plan_problems(seeded, plan, TEST_TEAM_ID)

    assert any("does not say who is sold" in p for p in problems)


# --- persistence and versioning ----------------------------------------------


def test_saving_a_plan_creates_version_one_and_makes_it_live(seeded):
    roadmap = save_roadmap(seeded, TEST_TEAM_ID, _legal_plan(), prompt="plan my next month")

    assert roadmap.version == 1
    assert roadmap.status == "live"
    assert roadmap.prompt == "plan my next month"
    assert roadmap.superseded_at is None
    assert get_live_roadmap(seeded, TEST_TEAM_ID).id == roadmap.id


def test_saving_again_supersedes_rather_than_overwrites(seeded):
    """The point of versioning. preferences.md asks to be able to say why a
    pick changed, and that is unanswerable against a row replaced in place."""
    first = save_roadmap(seeded, TEST_TEAM_ID, _legal_plan(), prompt="first ask")
    second = save_roadmap(
        seeded,
        TEST_TEAM_ID,
        _plan(
            _step(START_GW, "Other Mid", "Mid Two"),
            _step(START_GW + 2, "Cheap Mid", "Mid Three"),
        ),
        prompt="second ask",
    )

    versions = get_roadmap_versions(seeded, TEST_TEAM_ID)

    assert [v.version for v in versions] == [2, 1]
    assert second.version == 2
    assert get_live_roadmap(seeded, TEST_TEAM_ID).id == second.id

    old = next(v for v in versions if v.version == 1)
    assert old.id == first.id
    assert old.status == "superseded"
    assert old.superseded_at is not None
    assert "Replaced by version 2" in old.supersede_reason
    # And it is still fully readable -- the whole plan, not a stub.
    assert [s.pick.player_name for s in old.plan.steps] == ["Cheap Mid", "Other Mid"]


def test_only_one_roadmap_is_ever_live(seeded):
    for _ in range(3):
        save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    with seeded.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM roadmaps WHERE team_id = %s AND superseded_at IS NULL",
            (TEST_TEAM_ID,),
        )
        assert cur.fetchone()["n"] == 1


def test_a_roadmap_whose_every_step_has_passed_reads_as_completed(seeded):
    """A month-old plan must not keep looking authoritative."""
    save_roadmap(
        seeded,
        TEST_TEAM_ID,
        _plan(
            _step(START_GW - 5, "Cheap Mid", "Mid Two"),
            _step(START_GW - 4, "Other Mid", "Mid Three"),
        ),
    )

    assert get_live_roadmap(seeded, TEST_TEAM_ID).status == "completed"


# --- the check-in -------------------------------------------------------------


def test_check_in_says_so_when_there_is_no_roadmap(seeded):
    result = check_in(seeded, TEST_TEAM_ID)

    assert result.roadmap is None
    assert "No roadmap is stored" in result.summary


def test_check_in_on_an_unchanged_plan_reports_that_it_still_holds(seeded):
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    result = check_in(seeded, TEST_TEAM_ID)

    assert result.still_valid is True
    assert result.changes == []
    assert "still holds" in result.summary
    assert result.revalidation.legal is True


def test_check_in_separates_elapsed_steps_from_remaining_ones(seeded):
    """A plan that has simply progressed is not a plan that has broken."""
    save_roadmap(
        seeded,
        TEST_TEAM_ID,
        _plan(
            _step(START_GW - 1, "Cheap Mid", "Mid Two"),
            _step(START_GW + 2, "Other Mid", "Mid Three"),
        ),
    )

    result = check_in(seeded, TEST_TEAM_ID)

    assert result.elapsed_steps == [0]
    assert result.remaining_steps == [1]
    assert result.still_valid is True
    assert [c.kind for c in result.changes] == ["elapsed"]


def test_check_in_catches_a_gameweek_that_became_a_blank(seeded):
    """`fixtures` is wholesale replaced every run_ingestion, so a stored plan
    rots silently. Only the snapshot taken at save time turns "this is a
    blank" into "this BECAME a blank"."""
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    # The GW62 fixture is called off after the roadmap was stored.
    with seeded.cursor() as cur:
        cur.execute(
            "DELETE FROM fixtures WHERE gameweek = %s AND (home_team = %s OR away_team = %s)",
            (START_GW + 2, TARGET, TARGET),
        )
    seeded.commit()

    result = check_in(seeded, TEST_TEAM_ID)

    assert result.still_valid is False
    became = [c for c in result.fixture_changes if c.kind == "became_blank"]
    assert became and became[0].gameweek == START_GW + 2
    assert any(c.kind == "fixture" and c.step_index == 1 for c in result.changes)


def test_check_in_catches_a_gameweek_that_became_a_double(seeded):
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    with seeded.cursor() as cur:
        cur.execute(
            "INSERT INTO fixtures (id, gameweek, home_team, away_team, home_difficulty, "
            "away_difficulty) VALUES (%s, %s, %s, %s, 3, 3)",
            (FIXTURE_ID_BASE + 500, START_GW, TARGET, THIRD),
        )
    seeded.commit()

    result = check_in(seeded, TEST_TEAM_ID)

    doubles = [c for c in result.fixture_changes if c.kind == "became_double"]
    assert doubles and doubles[0].gameweek == START_GW


def test_check_in_catches_a_player_who_has_since_been_injured(seeded):
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    with seeded.cursor() as cur:
        cur.execute(
            "UPDATE fpl_players SET status = 'i' WHERE id = %s", (POOL_ID_BASE + 1,)
        )
    seeded.commit()

    result = check_in(seeded, TEST_TEAM_ID)

    assert result.still_valid is False
    injuries = [c for c in result.changes if c.kind == "injury"]
    assert injuries and injuries[0].player_name == "Cheap Mid"
    assert "now injured" in injuries[0].message


def test_check_in_catches_a_price_rise_that_broke_affordability(seeded):
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan())

    with seeded.cursor() as cur:
        cur.execute(
            "UPDATE fpl_players SET price = 30.0 WHERE id = %s", (POOL_ID_BASE + 1,)
        )
    seeded.commit()

    result = check_in(seeded, TEST_TEAM_ID)

    assert result.still_valid is False
    assert any(c.kind == "price" for c in result.changes)


def test_check_in_reports_violations_against_the_original_step_index(seeded):
    """Revalidation runs over a filtered forward slice, but the user and the
    model both refer to steps by where they sit in the roadmap they were
    shown."""
    save_roadmap(
        seeded,
        TEST_TEAM_ID,
        _plan(
            _step(START_GW - 1, "Other Mid", "Mid Three"),
            _step(START_GW + 1, "Cheap Mid", "Mid Two"),
        ),
    )

    with seeded.cursor() as cur:
        cur.execute(
            "UPDATE fpl_players SET price = 30.0 WHERE id = %s", (POOL_ID_BASE + 1,)
        )
    seeded.commit()

    result = check_in(seeded, TEST_TEAM_ID)

    broken = [c for c in result.changes if c.kind in {"price", "illegal"}]
    assert broken and broken[0].step_index == 1


# --- routes -------------------------------------------------------------------


class _RefusingReasoning:
    def reason(self, prompt, tools, system="", validate=None, history=None):
        raise AssertionError("roadmap reads must not make a reasoning call")


def test_roadmap_routes_read_without_a_reasoning_call(seeded, monkeypatch):
    """ADR-0015: every dashboard read is served from the database alone."""
    monkeypatch.setattr("fpl_coach.api.routes.MY_TEAM_ID", TEST_TEAM_ID)
    save_roadmap(seeded, TEST_TEAM_ID, _legal_plan(), prompt="plan me")

    app.dependency_overrides[get_reasoning] = _RefusingReasoning
    try:
        with TestClient(app) as client:
            live = client.get("/team/roadmap")
            versions = client.get("/team/roadmap/versions")
            checkin = client.get("/team/roadmap/check-in")
    finally:
        app.dependency_overrides.clear()

    assert live.status_code == versions.status_code == checkin.status_code == 200
    assert live.json()["version"] == 1
    assert live.json()["status"] == "live"
    assert len(versions.json()) == 1
    assert checkin.json()["still_valid"] is True


def test_roadmap_route_returns_null_when_none_is_stored(seeded, monkeypatch):
    monkeypatch.setattr("fpl_coach.api.routes.MY_TEAM_ID", TEST_TEAM_ID)
    app.dependency_overrides[get_reasoning] = _RefusingReasoning
    try:
        with TestClient(app) as client:
            response = client.get("/team/roadmap")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() is None
