from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek
from fpl_coach.models import Instinct, Instincts
from fpl_coach.reasoning.base import ToolSpec


def get_instincts(conn: Connection, team_id: int) -> Instincts:
    # Must match log_instinct's tagging, and for the same reason: the notes
    # worth surfacing are the ones about the deadline the user can still act
    # on, not the gameweek gameweek_state last happened to persist.
    gameweek = planning_gameweek(conn, team_id)
    if gameweek is None:
        return Instincts(notes=[])

    with conn.cursor() as cur:
        cur.execute(
            "SELECT text, created_at FROM instincts WHERE team_id = %s AND gameweek = %s "
            "ORDER BY created_at",
            (team_id, gameweek),
        )
        rows = cur.fetchall()

    return Instincts(
        notes=[Instinct(text=row["text"], logged_at=row["created_at"].isoformat()) for row in rows]
    )


def make_get_instincts_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_instincts",
        description=(
            "Returns the user's own early-week Instinct notes logged this gameweek, before "
            "deadline-day content and pressure build up (e.g. \"thinking Rogers to Enzo\"). "
            "Call this whenever the question concerns a transfer or captaincy decision. If a "
            "returned Instinct is relevant to the current question, your evidence or risks "
            "must quote it and explicitly ask the user whether their thinking has genuinely "
            "evolved since logging it or whether they might be chasing a bandwagon -- never "
            "silently override it and never silently just agree with it. Empty when nothing "
            "has been logged this gameweek."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_instincts(conn, team_id),
    )
