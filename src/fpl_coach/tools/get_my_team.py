from psycopg import Connection

from fpl_coach.models import Player, Team
from fpl_coach.reasoning.base import ToolSpec


def get_my_team(conn: Connection, team_id: int) -> Team:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM teams WHERE id = %s", (team_id,))
        team_row = cur.fetchone()
        if team_row is None:
            raise ValueError(f"No team with id {team_id}")

        cur.execute(
            # Issue 05: fpl_element_id (when set) puts squad players in the
            # same id namespace as the full pool (fpl_players.id), so a
            # Recommendation's pick can reference either uniformly. Falls
            # back to the row's own serial id for manually seeded rows.
            "SELECT COALESCE(fpl_element_id, id) AS id, name, position, club, price, "
            "ownership, form, status, news "
            "FROM players WHERE team_id = %s ORDER BY players.id",
            (team_id,),
        )
        players = [Player(**row) for row in cur.fetchall()]

    return Team(id=team_row["id"], name=team_row["name"], players=players)


def make_get_my_team_tool(conn: Connection, team_id: int) -> ToolSpec:
    """v1 is single-user (one team), so the tool takes no arguments — it
    always returns that one seeded team, bound via closure."""
    return ToolSpec(
        name="get_my_team",
        description=(
            "Returns the user's current FPL squad: team name and each player's id, "
            "name, position, club, price, ownership %, form, and injury status "
            "(a=available, d=doubtful, i=injured, s=suspended, u=unavailable, "
            "n=not in squad; null for not-yet-ingested data)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_my_team(conn, team_id),
    )
