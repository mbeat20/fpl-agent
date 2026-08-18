from psycopg import Connection

from fpl_coach.models import CreatorOpinions, CreatorStanceView
from fpl_coach.reasoning.base import ToolSpec

_TRUST_ORDER = {"high": 0, "medium": 1, "low": 2}

_OPINIONS_SQL = """
SELECT c.name AS creator_name, c.trust_tier, s.stance, s.confidence, s.reason
FROM creator_stances s
JOIN creators c ON c.id = s.creator_id
WHERE s.player_name ILIKE %s
ORDER BY s.created_at DESC
"""


def get_creator_opinions(conn: Connection, player_name: str) -> CreatorOpinions:
    """Distinct from ticket 09's search_creator_opinions (pgvector semantic
    search over embedded content) -- this is an exact-ish name lookup over
    the structured Stance rows already extracted."""
    with conn.cursor() as cur:
        cur.execute(_OPINIONS_SQL, (f"%{player_name}%",))
        rows = cur.fetchall()

    stances = sorted(
        (
            CreatorStanceView(
                creator_name=row["creator_name"],
                trust_tier=row["trust_tier"],
                stance=row["stance"],
                confidence=float(row["confidence"]),
                reason=row["reason"],
            )
            for row in rows
        ),
        key=lambda s: _TRUST_ORDER.get(s.trust_tier, len(_TRUST_ORDER)),
    )
    return CreatorOpinions(player_name=player_name, stances=stances)


def make_get_creator_opinions_tool(conn: Connection) -> ToolSpec:
    return ToolSpec(
        name="get_creator_opinions",
        description=(
            "Returns what trusted creators think about a given FPL player: each "
            "creator's stance (buy/sell/hold/captain), confidence, and reason, sorted "
            "with higher trust-tier creators first -- weigh those opinions more heavily."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "player_name": {"type": "string", "description": "player name, full or partial"}
            },
            "required": ["player_name"],
        },
        handler=lambda player_name: get_creator_opinions(conn, player_name),
    )
