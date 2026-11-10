"""Shared next-fixture lookup, used by get_injury_news.py (issue 07) and
recommend_transfers.py (issue 11)."""

from psycopg import Connection

_NEXT_FIXTURE_SQL = """
WITH next_fixtures AS (
    SELECT home_team AS club, away_team AS opponent, home_difficulty AS difficulty,
           kickoff_time
    FROM fixtures
    UNION ALL
    SELECT away_team AS club, home_team AS opponent, away_difficulty AS difficulty,
           kickoff_time
    FROM fixtures
)
SELECT DISTINCT ON (club) club, opponent, difficulty
FROM next_fixtures
WHERE kickoff_time > now()
ORDER BY club, kickoff_time
"""


def next_fixture_by_club(conn: Connection) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(_NEXT_FIXTURE_SQL)
        return {row["club"]: row for row in cur.fetchall()}


_UPCOMING_GAMEWEEK_SQL = """
SELECT MIN(gameweek) AS gameweek FROM fixtures WHERE kickoff_time > now()
"""


def upcoming_gameweek(conn: Connection) -> int | None:
    """The earliest gameweek that still has an unplayed fixture, derived from
    wall-clock time rather than from gameweek_state.

    A safety net for consumers that must not act on a finished gameweek.
    gameweek_state only advances when ingestion runs (ADR-0002: no
    scheduler), so between a gameweek ending and the next ingestion it names
    a gameweek whose fixtures have all been played. Fixture kickoff times
    are already in the database and need no API call to stay honest.

    None when no fixture is scheduled ahead -- season over, or fixtures have
    never been ingested.
    """
    with conn.cursor() as cur:
        cur.execute(_UPCOMING_GAMEWEEK_SQL)
        row = cur.fetchone()
    return row["gameweek"] if row else None
