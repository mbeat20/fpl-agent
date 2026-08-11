from psycopg import Connection

from fpl_coach.models import Player
from fpl_coach.reasoning.base import ToolSpec

_PLAYER_COLUMNS = "id, name, position, club, price, ownership, form, status, news, xg, xa"


def find_player(conn: Connection, name: str) -> Player | None:
    """Case-insensitive lookup by web name or full name across the whole FPL
    player pool (issue 05) — not just the user's squad. Exact match first,
    falling back to a substring match so "Salah" finds "Mohamed Salah"."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_PLAYER_COLUMNS} FROM fpl_players "
            "WHERE lower(name) = lower(%s) OR lower(full_name) = lower(%s) LIMIT 1",
            (name, name),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                f"SELECT {_PLAYER_COLUMNS} FROM fpl_players "
                "WHERE name ILIKE %s OR full_name ILIKE %s ORDER BY name LIMIT 1",
                (f"%{name}%", f"%{name}%"),
            )
            row = cur.fetchone()
    return Player(**row) if row is not None else None


def get_player(conn: Connection, name: str) -> Player:
    player = find_player(conn, name)
    if player is None:
        raise ValueError(f"No FPL player found matching {name!r}")
    return player


def make_get_player_tool(conn: Connection) -> ToolSpec:
    return ToolSpec(
        name="get_player",
        description=(
            "Looks up any FPL player by name (not just the user's squad) — "
            "id, position, club, price, ownership %, form, injury status, and "
            "Understat xG/xA (null if unmatched or not yet ingested)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "player name, full or partial"}
            },
            "required": ["name"],
        },
        handler=lambda name: get_player(conn, name),
    )
