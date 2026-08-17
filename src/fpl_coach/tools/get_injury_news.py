from psycopg import Connection

from fpl_coach.models import PlayerRisk, SquadRiskSummary
from fpl_coach.reasoning.base import ToolSpec

_INJURY_STATUSES = {"d", "i", "s", "u"}  # doubtful, injured, suspended, unavailable
_HIGH_DIFFICULTY = 4  # FPL's own 1-5 FDR scale; 4-5 is a tough fixture

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


def get_injury_news(conn: Connection, team_id: int) -> SquadRiskSummary:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(fpl_element_id, id) AS id, name, club, status, news "
            "FROM players WHERE team_id = %s ORDER BY players.id",
            (team_id,),
        )
        squad = cur.fetchall()

        cur.execute(_NEXT_FIXTURE_SQL)
        next_fixture_by_club = {row["club"]: row for row in cur.fetchall()}

    players = []
    for row in squad:
        fixture = next_fixture_by_club.get(row["club"])
        difficulty = fixture["difficulty"] if fixture else None
        players.append(
            PlayerRisk(
                player_id=row["id"],
                player_name=row["name"],
                status=row["status"],
                news=row["news"],
                next_opponent=fixture["opponent"] if fixture else None,
                next_fixture_difficulty=difficulty,
                injury_risk=row["status"] in _INJURY_STATUSES,
                # No ingested fixture, or a fixture whose difficulty rating isn't
                # set yet (both nullable — schema.sql), reads as a risk either way.
                fixture_risk=difficulty is None or difficulty >= _HIGH_DIFFICULTY,
            )
        )

    return SquadRiskSummary(players=players)


def make_get_injury_news_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_injury_news",
        description=(
            "Returns a squad-wide risk summary: each player's injury/doubt/suspension "
            "status (from the FPL API's own flags) plus fixture-driven risk — a tough "
            "upcoming opponent (FPL difficulty 4-5) or no fixture at all this gameweek "
            "(a blank gameweek)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_injury_news(conn, team_id),
    )
