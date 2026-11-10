from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import CreatorOpinions, CreatorStanceView
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.stance_ranking import (
    DEFAULT_LIMIT,
    age_days,
    describe_recency,
    is_current,
    stance_weight,
)

# Issue 17: the JOIN out to creator_content is what makes a Stance citable.
# s.id is handed back as citation_id -- the reasoning layer's submit tool
# accepts that id and looks the attribution up itself, so the model never
# authors a URL (ADR-0004's discipline, applied to sources instead of picks).
#
# Issue 18: s.gameweek and cc.published_at join the projection so a Stance can
# be dated and ranked. The ORDER BY here is only a stable tiebreak -- the real
# ordering is stance_weight in Python, which needs wall-clock decay that SQL
# would have to recompute per row anyway.
_OPINIONS_SQL = """
SELECT s.id AS citation_id, c.name AS creator_name, c.trust_tier, s.stance,
       s.confidence, s.reason, s.start_seconds, s.gameweek,
       cc.source_url, cc.published_at
FROM creator_stances s
JOIN creators c ON c.id = s.creator_id
JOIN creator_content cc ON cc.id = s.source_content_id
WHERE s.player_name ILIKE %s
ORDER BY cc.published_at DESC NULLS LAST, s.id DESC
"""

# How far back a Stance is still allowed to speak to the gameweek being
# planned. One gameweek back: last week's "buy" is genuinely informative
# context, anything older is about fixtures and prices that have since moved.
# Older rows are counted and reported, never deleted (ticket 19 and ADR-0012
# both read them).
_MAX_GAMEWEEKS_BACK = 1

# A hard age floor as well as the gameweek test. The gameweek tag says which
# deadline a Stance was fetched before, which for pre-season content ingested
# in August is GW1 -- technically true and completely misleading for a post
# from 2022. The ticket is explicit that "no trusted creator has mentioned him
# recently" is a better answer than reaching further back to fill the list.
_MAX_AGE_DAYS = 30.0

# At most this many Stances from any one creator. Recency ranking alone let a
# single prolific account take every slot -- measured on the live database,
# all 8 returned Stances for one player came from the same creator, which
# reads as consensus and is nothing of the sort. Ticket 19 counts consensus
# properly; this only stops one voice impersonating several.
_MAX_PER_CREATOR = 2


def _iso(value) -> str | None:
    """published_at arrives as a datetime from psycopg and has to survive a
    JSONB round-trip through team_suggestions, so it is normalised to an ISO
    string at the boundary rather than left as a type only Python understands."""
    return value.isoformat() if value is not None else None


def _is_recent_enough(gameweek: int | None, planning: int | None, published_at) -> bool:
    """Held back if EITHER test places the Stance in the past: too many
    gameweeks back, or simply too old. A Stance we cannot place at all --
    no gameweek and no date -- is kept rather than hidden, and ranked down by
    stance_weight instead."""
    days = age_days(published_at)
    if days is not None and days > _MAX_AGE_DAYS:
        return False
    if gameweek is None or planning is None:
        return True
    return gameweek >= planning - _MAX_GAMEWEEKS_BACK


def get_creator_opinions(
    conn: Connection, player_name: str, team_id: int, limit: int = DEFAULT_LIMIT
) -> CreatorOpinions:
    """Distinct from ticket 09's search_creator_opinions (pgvector semantic
    search over embedded content) -- this is an exact-ish name lookup over
    the structured Stance rows already extracted.

    Issue 18: bounded and recency-ranked. The counts come back with the
    stances so the reasoning layer can tell a top slice from the whole
    picture, and so "nobody has said anything about him lately" is a
    distinct answer rather than an empty list."""
    planning = resolve_planning_gameweek(conn, team_id)

    with conn.cursor() as cur:
        cur.execute(_OPINIONS_SQL, (f"%{player_name}%",))
        rows = cur.fetchall()

    recent = [
        row
        for row in rows
        if _is_recent_enough(row["gameweek"], planning, row["published_at"])
    ]

    by_weight = sorted(
        recent,
        key=lambda row: stance_weight(row["trust_tier"], row["published_at"]),
        reverse=True,
    )

    ranked = []
    per_creator: dict[str, int] = {}
    for row in by_weight:
        seen = per_creator.get(row["creator_name"], 0)
        if seen >= _MAX_PER_CREATOR:
            continue
        per_creator[row["creator_name"]] = seen + 1
        ranked.append(row)
        if len(ranked) == limit:
            break

    stances = [
        CreatorStanceView(
            creator_name=row["creator_name"],
            trust_tier=row["trust_tier"],
            stance=row["stance"],
            confidence=float(row["confidence"]),
            reason=row["reason"],
            citation_id=row["citation_id"],
            source_url=row["source_url"],
            start_seconds=row["start_seconds"],
            published_at=_iso(row["published_at"]),
            gameweek=row["gameweek"],
            age_days=age_days(row["published_at"]),
            recency=describe_recency(row["published_at"], row["gameweek"], planning),
            is_current_gameweek=is_current(row["gameweek"], planning),
        )
        for row in ranked
    ]

    older_not_shown = len(rows) - len(recent)
    note = None
    if not stances and older_not_shown:
        newest_stale = max(
            (row["gameweek"] for row in rows if row["gameweek"] is not None), default=None
        )
        since = f"since GW{newest_stale}" if newest_stale is not None else "recently"
        note = (
            f"No trusted creator has said anything about {player_name} {since}. "
            f"{older_not_shown} older stance(s) exist but are too stale to bear on "
            "the gameweek being planned -- treat this as an absence of current "
            "coverage, not as agreement."
        )

    return CreatorOpinions(
        player_name=player_name,
        stances=stances,
        total_matched=len(rows),
        returned=len(stances),
        older_not_shown=older_not_shown,
        planning_gameweek=planning,
        note=note,
    )


def make_get_creator_opinions_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_creator_opinions",
        description=(
            "Returns what trusted creators have recently said about one named player: "
            "their stance (buy/sell/hold/captain), confidence, reasoning and trust tier -- "
            "weigh higher-trust creators' opinions more heavily. "
            "Results are ranked by recency and trust tier and capped, so check "
            "total_matched against returned to see whether you are looking at a slice. "
            "Every stance carries a `recency` phrase saying how old it is and which "
            "gameweek it was published for -- READ IT. A stance published for an "
            "already-played gameweek predates the current deadline and must not be "
            "treated as current advice; when an older stance and a newer one disagree, "
            "the newer one wins unless you can say specifically why it shouldn't. "
            "An empty list with a `note` means nobody has covered this player lately, "
            "which is an absence of evidence and not agreement. Pass a stance's "
            "citation_id back on the matching evidence line in submit_recommendation "
            "or submit_plan so the user can click through to where it was said."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "player_name": {"type": "string", "description": "the player's name"}
            },
            "required": ["player_name"],
        },
        handler=lambda player_name: get_creator_opinions(conn, player_name, team_id),
    )
