import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.tools.get_injury_news import get_injury_news, make_get_injury_news_tool

TEST_TEAM_ID = 9101
TEST_FIXTURE_IDS = (9101, 9102)
# get_injury_news scans the *whole* fixtures table for each club's next
# fixture, so isolation must clear every row touching these clubs, not just
# the rows this fixture itself inserts — e.g. a real ingested LIV fixture
# (ticket 03) sharing the table would otherwise silently win the DISTINCT ON
# and falsify next_opponent/difficulty for these tests.
TEST_CLUBS = ["LIV", "MCI", "ARS", "TOT", "CHE"]

FUTURE = datetime.now(timezone.utc) + timedelta(days=7)


def _delete_test_club_fixtures(conn) -> None:
    conn.execute(
        "DELETE FROM fixtures WHERE home_team = ANY(%s) OR away_team = ANY(%s)",
        (TEST_CLUBS, TEST_CLUBS),
    )


@pytest.fixture
def squad_with_risks():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
            _delete_test_club_fixtures(conn)
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Risk Team")
            )
            cur.executemany(
                "INSERT INTO players (team_id, name, position, club, price, status, news) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    # Injured, easy next fixture (difficulty 2) -> injury risk only.
                    (TEST_TEAM_ID, "Injured Easy", "MID", "LIV", 10.0, "i", "Hamstring"),
                    # Fit, tough next fixture (difficulty 5) -> fixture risk only.
                    (TEST_TEAM_ID, "Fit Hard Fixture", "FWD", "MCI", 12.0, "a", None),
                    # Fit, no ingested fixture at all -> blank-gameweek fixture risk.
                    (TEST_TEAM_ID, "Blank Gameweek", "DEF", "ARS", 5.0, "a", None),
                    # Fit, has a fixture but its difficulty rating is NULL
                    # (schema.sql declares both difficulty columns nullable).
                    (TEST_TEAM_ID, "Unrated Fixture", "DEF", "TOT", 6.0, "a", None),
                ],
            )
            cur.execute(
                "INSERT INTO fixtures "
                "(id, gameweek, home_team, away_team, home_difficulty, away_difficulty, "
                " kickoff_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (TEST_FIXTURE_IDS[0], 10, "LIV", "MCI", 2, 5, FUTURE),
            )
            cur.execute(
                "INSERT INTO fixtures "
                "(id, gameweek, home_team, away_team, home_difficulty, away_difficulty, "
                " kickoff_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (TEST_FIXTURE_IDS[1], 10, "TOT", "CHE", None, None, FUTURE),
            )
        conn.commit()
    yield TEST_TEAM_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        _delete_test_club_fixtures(conn)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def test_get_injury_news_covers_the_whole_squad_not_just_one_player(squad_with_risks):
    with _conn() as conn:
        summary = get_injury_news(conn, squad_with_risks)

    assert {p.player_name for p in summary.players} == {
        "Injured Easy",
        "Fit Hard Fixture",
        "Blank Gameweek",
        "Unrated Fixture",
    }


def test_injury_status_is_surfaced_independent_of_fixture_difficulty(squad_with_risks):
    with _conn() as conn:
        summary = get_injury_news(conn, squad_with_risks)

    by_name = {p.player_name: p for p in summary.players}
    injured = by_name["Injured Easy"]
    assert injured.status == "i"
    assert injured.injury_risk is True
    assert injured.fixture_risk is False  # difficulty 2, below the threshold


def test_tough_upcoming_fixture_is_flagged_as_fixture_risk(squad_with_risks):
    with _conn() as conn:
        summary = get_injury_news(conn, squad_with_risks)

    by_name = {p.player_name: p for p in summary.players}
    hard_fixture = by_name["Fit Hard Fixture"]
    assert hard_fixture.injury_risk is False
    assert hard_fixture.fixture_risk is True
    assert hard_fixture.next_opponent == "LIV"
    assert hard_fixture.next_fixture_difficulty == 5


def test_no_ingested_fixture_is_flagged_as_a_blank_gameweek_risk(squad_with_risks):
    with _conn() as conn:
        summary = get_injury_news(conn, squad_with_risks)

    by_name = {p.player_name: p for p in summary.players}
    blank = by_name["Blank Gameweek"]
    assert blank.next_opponent is None
    assert blank.next_fixture_difficulty is None
    assert blank.fixture_risk is True


def test_a_fixture_with_no_difficulty_rating_does_not_crash_and_is_flagged_as_risk(
    squad_with_risks,
):
    """Both difficulty columns are nullable (schema.sql) — a fixture whose
    rating hasn't been set must not raise and should still read as a risk,
    same as having no fixture at all."""
    with _conn() as conn:
        summary = get_injury_news(conn, squad_with_risks)

    by_name = {p.player_name: p for p in summary.players}
    unrated = by_name["Unrated Fixture"]
    assert unrated.next_opponent == "CHE"
    assert unrated.next_fixture_difficulty is None
    assert unrated.fixture_risk is True


def test_make_get_injury_news_tool_handler_returns_the_summary(squad_with_risks):
    with _conn() as conn:
        tool = make_get_injury_news_tool(conn, squad_with_risks)
        summary = tool.handler()

    assert len(summary.players) == 4
