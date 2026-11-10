"""Issue 21: fixture runs, blanks and doubles.

The failure this guards against: `next_fixture_by_club`'s `DISTINCT ON (club)`
keeps exactly one row per club, so a gameweek with no fixture and a gameweek
with two both come back looking like a gameweek with one. Every Recommendation
and every TransferPlan step was built on that view, which is why multi-week
planning was plausible rather than credible -- and why a plan could aim a
Bench Boost at a blank.

Seeds its own clubs, its own fixtures and its own team, on gameweek and
fixture ids far outside anything real. `fixtures` is a global table that
`run_ingestion` wipes wholesale, so nothing here may depend on what the live
table happens to hold.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.fixtures import next_fixture_by_club
from fpl_coach.tools.get_fixture_run import MAX_WEEKS, get_fixture_run

TEST_TEAM_ID = 90221
START_GW = 80
FIXTURE_ID_BASE = 990_000
# Short names that cannot collide with a real Premier League club.
HOME, RIVAL, QUIET, BUSY = "ZZA", "ZZB", "ZZC", "ZZD"
TEST_CLUBS = (HOME, RIVAL, QUIET, BUSY)
TEST_CLUB_IDS = (9901, 9902, 9903, 9904)

NOW = datetime.now(timezone.utc)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM fixtures WHERE id >= %s", (FIXTURE_ID_BASE,))
        conn.execute("DELETE FROM clubs WHERE id = ANY(%s)", (list(TEST_CLUB_IDS),))
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))


@pytest.fixture
def seeded_fixtures():
    """A five-gameweek world with one of everything the ticket names.

    ZZA: a normal run, with a BLANK in GW82 and a DOUBLE in GW83.
    ZZB: a normal fixture every gameweek.
    ZZC: no fixtures at all in the horizon -- blank every week.
    ZZD: one fixture with no gameweek assigned (unscheduled, NOT a blank).
    """
    _cleanup()
    with _conn() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Runs"))
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, START_GW),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clubs (id, name, short_name) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET short_name = EXCLUDED.short_name",
                [
                    (9901, "Zed Athletic", HOME),
                    (9902, "Zed Rovers", RIVAL),
                    (9903, "Zed Quiet", QUIET),
                    (9904, "Zed Busy", BUSY),
                ],
            )

            rows = [
                # (id_offset, gameweek, home, away, home_fdr, away_fdr, days_out)
                (0, START_GW, HOME, RIVAL, 2, 4, 1),
                (1, START_GW + 1, RIVAL, HOME, 3, 5, 8),
                # GW82: ZZA blank -- ZZB plays somebody else entirely.
                (2, START_GW + 2, RIVAL, BUSY, 2, 2, 15),
                # GW83: ZZA plays twice -- a double.
                (3, START_GW + 3, HOME, RIVAL, 1, 5, 22),
                (4, START_GW + 3, BUSY, HOME, 3, 2, 23),
                (5, START_GW + 4, HOME, BUSY, 4, 3, 29),
                (6, START_GW + 4, RIVAL, QUIET, 2, 3, 30),
            ]
            cur.executemany(
                "INSERT INTO fixtures (id, gameweek, home_team, away_team, "
                "home_difficulty, away_difficulty, kickoff_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        FIXTURE_ID_BASE + offset,
                        gameweek,
                        home,
                        away,
                        home_fdr,
                        away_fdr,
                        NOW + timedelta(days=days),
                    )
                    for offset, gameweek, home, away, home_fdr, away_fdr, days in rows
                ],
            )
            # An unscheduled fixture: FPL has not given it a gameweek yet.
            cur.execute(
                "INSERT INTO fixtures (id, gameweek, home_team, away_team, "
                "home_difficulty, away_difficulty, kickoff_time) "
                "VALUES (%s, NULL, %s, %s, %s, %s, NULL)",
                (FIXTURE_ID_BASE + 50, BUSY, QUIET, 2, 3),
            )
        conn.commit()

        yield conn

    _cleanup()


def _run(conn, clubs, weeks=5):
    return get_fixture_run(conn, clubs, TEST_TEAM_ID, weeks=weeks)


def _for(result, club):
    return next(item for item in result.clubs if item.club == club)


def test_a_run_returns_every_fixture_with_opponent_home_away_and_difficulty(
    seeded_fixtures,
):
    run = _run(seeded_fixtures, [HOME])
    club = _for(run, HOME)

    assert run.from_gameweek == START_GW
    assert run.to_gameweek == START_GW + 4
    assert [week.gameweek for week in club.gameweeks] == list(range(START_GW, START_GW + 5))

    first = club.gameweeks[0].fixtures[0]
    assert (first.opponent, first.is_home, first.difficulty) == (RIVAL, True, 2)
    # Away in GW81, and FPL rates the two sides of a fixture differently --
    # taking the wrong side's number inverts the judgement.
    second = club.gameweeks[1].fixtures[0]
    assert (second.opponent, second.is_home, second.difficulty) == (RIVAL, False, 5)


def test_a_blank_gameweek_is_reported_explicitly(seeded_fixtures):
    run = _run(seeded_fixtures, [HOME])
    club = _for(run, HOME)

    blank = club.gameweeks[2]
    assert blank.gameweek == START_GW + 2
    assert blank.is_blank is True
    assert blank.count == 0
    assert blank.fixtures == []
    assert club.blanks == [START_GW + 2]
    assert "no fixture in GW82" in club.note
    assert "Bench Boost" in club.note


def test_a_double_gameweek_reports_both_fixtures(seeded_fixtures):
    run = _run(seeded_fixtures, [HOME])
    club = _for(run, HOME)

    double = club.gameweeks[3]
    assert double.is_double is True
    assert double.count == 2
    assert {f.opponent for f in double.fixtures} == {RIVAL, BUSY}
    assert {f.is_home for f in double.fixtures} == {True, False}
    assert club.doubles == [START_GW + 3]


def test_an_unscheduled_fixture_is_not_a_blank(seeded_fixtures):
    """The ticket is explicit: a nullable `fixtures.gameweek` and a club
    genuinely having no fixture are different facts, and next_fixture_by_club
    already collapses them."""
    run = _run(seeded_fixtures, [BUSY])
    club = _for(run, BUSY)

    assert club.unscheduled_fixtures == 1
    assert "unscheduled, NOT a blank" in club.note
    # And the unscheduled fixture is not counted into any gameweek: ZZD has
    # three scheduled ones (GW82 away, GW83 home, GW84 away).
    assert club.fixtures_count == 3


def test_a_club_with_no_fixtures_at_all_blanks_every_gameweek(seeded_fixtures):
    run = _run(seeded_fixtures, [QUIET])
    club = _for(run, QUIET)

    assert club.blanks == list(range(START_GW, START_GW + 4))
    assert club.fixtures_count == 1  # only the GW84 trip to ZZB
    assert club.total_difficulty == 3


def test_the_aggregate_and_the_individual_fixtures_both_come_back(seeded_fixtures):
    """AC: an average alone hides a run that is three easy games then a wall,
    which is exactly the shape a transfer plan should be built around."""
    run = _run(seeded_fixtures, [HOME])
    club = _for(run, HOME)

    # GW80 h(2), GW81 a(5), GW82 blank, GW83 h(1) + a(2), GW84 h(4)
    assert club.fixtures_count == 5
    assert club.total_difficulty == 2 + 5 + 1 + 2 + 4
    # Per FIXTURE, not per gameweek -- dividing by gameweeks would score the
    # blank as difficulty 0 and make a club with no fixture look easy.
    assert club.average_difficulty == 2.8
    assert club.difficulty_complete is True
    assert [f.difficulty for week in club.gameweeks for f in week.fixtures] == [2, 5, 1, 2, 4]


def test_several_clubs_are_comparable_in_one_call(seeded_fixtures):
    """AC: the reasoning layer must not have to burn a turn per club."""
    run = _run(seeded_fixtures, [HOME, RIVAL, QUIET])

    assert [club.club for club in run.clubs] == [HOME, RIVAL, QUIET]
    assert _for(run, RIVAL).blanks == []
    assert _for(run, HOME).blanks == [START_GW + 2]


def test_a_club_can_be_named_by_its_full_name_or_short_name(seeded_fixtures):
    by_short = _run(seeded_fixtures, [HOME])
    by_full = _run(seeded_fixtures, ["Zed Athletic"])

    assert _for(by_full, HOME).total_difficulty == _for(by_short, HOME).total_difficulty
    assert by_full.clubs[0].name == "Zed Athletic"


def test_an_unknown_club_is_echoed_back_not_silently_dropped(seeded_fixtures):
    """A club missing from the output reads as a club with no fixtures, which
    reads as a blank, which is a decision."""
    run = _run(seeded_fixtures, [HOME, "Nowhere United"])

    assert run.unknown_clubs == ["Nowhere United"]
    assert [club.club for club in run.clubs] == [HOME]


def test_a_missing_difficulty_is_unknown_not_easy(seeded_fixtures):
    seeded_fixtures.execute(
        "UPDATE fixtures SET home_difficulty = NULL WHERE id = %s", (FIXTURE_ID_BASE,)
    )
    seeded_fixtures.commit()

    club = _for(_run(seeded_fixtures, [HOME]), HOME)

    assert club.difficulty_complete is False
    assert "not as easy" in club.note
    # The aggregate covers only the rated fixtures -- a NULL must never be
    # summed as a zero, which would read as the easiest possible fixture.
    assert club.total_difficulty == 5 + 1 + 2 + 4


def test_the_horizon_starts_at_the_planning_gameweek(seeded_fixtures):
    """Not "the next fixture in the table". The two differ in exactly the
    window where planning happens, and starting on a gameweek whose deadline
    has passed offers a fixture nobody can act on as step one of a plan."""
    run = _run(seeded_fixtures, [HOME], weeks=3)

    assert (run.from_gameweek, run.to_gameweek, run.weeks) == (START_GW, START_GW + 2, 3)
    assert len(_for(run, HOME).gameweeks) == 3


def test_the_horizon_is_bounded(seeded_fixtures):
    assert _run(seeded_fixtures, [HOME], weeks=99).weeks == MAX_WEEKS
    assert _run(seeded_fixtures, [HOME], weeks=0).weeks == 1


def test_next_fixture_by_club_is_unchanged(seeded_fixtures):
    """AC: tickets 07 and 11's existing single-fixture callers are untouched.
    This is an additional capability, not a refactor."""
    by_club = next_fixture_by_club(seeded_fixtures)

    assert by_club[HOME]["opponent"] == RIVAL
    assert by_club[HOME]["difficulty"] == 2
    # Still exactly one row per club -- the shape its callers expect.
    assert isinstance(by_club[RIVAL], dict)
