from psycopg import Connection

from fpl_coach.models import PlayerComparison
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.tools.get_player import find_player


def compare_players(conn: Connection, names: list[str]) -> PlayerComparison:
    players = [p for p in (find_player(conn, name) for name in names) if p is not None]
    if not players:
        raise ValueError(f"None of {names!r} matched an FPL player")
    return PlayerComparison(players=players)


def make_compare_players_tool(conn: Connection) -> ToolSpec:
    return ToolSpec(
        name="compare_players",
        description=(
            "Side-by-side comparison of two or more FPL players (any player, not "
            "just the user's squad) — price, ownership %, form, injury status, and "
            "Understat xG/xA for each. Unmatched names are silently skipped."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "description": "two or more player names, full or partial",
                }
            },
            "required": ["names"],
        },
        handler=lambda names: compare_players(conn, names),
    )
