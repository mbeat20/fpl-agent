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
