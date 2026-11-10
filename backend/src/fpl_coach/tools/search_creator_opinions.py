from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.ingestion.voyage_client import VoyageClient, vector_literal
from fpl_coach.models import SemanticCreatorMatch, SemanticCreatorOpinions
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.stance_ranking import (
    DEFAULT_LIMIT,
    age_days,
    describe_recency,
    is_current,
    stance_weight,
)

# Two-stage: narrow to the nearest chunks first, then join out to Stances.
# Joining chunks -> creator_stances directly and taking the top `limit` rows
# would let one verbose post's multiple Stances crowd out every other
# creator -- CTE-then-join keeps the candidate pool spread across posts
# (ADR-0005: many-Stances-to-one-source, not 1:1). A chunk with no extracted
# Stance (a content row can yield zero, per ADR-0005) is simply dropped by
# the join -- ticket 09's AC requires resolving back to structured fields,
# not returning raw text alone.
#
# Issue 17: creator_content joins in for the citation. start_seconds prefers
# the Stance's own timestamp and falls back to the matched chunk's -- the
# chunk is what similarity actually found, so on a transcript whose Stance
# was extracted untimed it still lands the user near the claim rather than at
# minute zero of a 40-minute video.
_SEARCH_SQL = """
WITH nearest_chunks AS (
    SELECT content_id, chunk_text, start_seconds,
           embedding <=> %(query_embedding)s::vector AS distance
    FROM content_chunks
    ORDER BY distance
    LIMIT %(chunk_pool)s
)
SELECT s.id AS citation_id, c.name AS creator_name, c.trust_tier, s.stance,
       s.confidence, s.reason, nc.chunk_text AS matched_text, nc.distance,
       COALESCE(s.start_seconds, nc.start_seconds) AS start_seconds,
       s.gameweek, cc.source_url, cc.published_at
FROM nearest_chunks nc
JOIN creator_stances s ON s.source_content_id = nc.content_id
JOIN creators c ON c.id = s.creator_id
JOIN creator_content cc ON cc.id = nc.content_id
ORDER BY nc.distance
LIMIT %(candidate_pool)s
"""


def _similarity(distance: float) -> float:
    """Cosine distance (0 = identical) as a 0-1 relevance score. Clamped
    rather than allowed to go negative: an opposite-direction embedding is no
    less irrelevant than an orthogonal one, and a negative factor would
    invert the recency weighting it gets multiplied by."""
    return max(0.0, 1.0 - float(distance))


def search_creator_opinions(
    conn: Connection,
    voyage_client: VoyageClient,
    query: str,
    team_id: int,
    limit: int = DEFAULT_LIMIT,
) -> SemanticCreatorOpinions:
    """Issue 18: candidates are still gathered by embedding distance, but the
    returned ordering is similarity scaled by the same trust/recency weight
    get_creator_opinions uses.

    Ranking a loose query on recency alone would hand back today's least
    relevant post; ranking on similarity alone is what let a 2022 take lead
    the list. The product is the honest compromise -- a stale hit has to be
    substantially more relevant to outrank a current one.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    embedding = voyage_client.embed([query])[0]
    with conn.cursor() as cur:
        cur.execute(
            _SEARCH_SQL,
            {
                "query_embedding": vector_literal(embedding),
                "chunk_pool": max(limit * 3, 10),
                # Over-fetch so re-ranking has something to choose between:
                # cutting to `limit` in SQL by distance alone would discard
                # the fresher, slightly-further hit before it could win.
                "candidate_pool": max(limit * 4, 20),
            },
        )
        rows = cur.fetchall()

    ranked = sorted(
        rows,
        key=lambda row: _similarity(row["distance"])
        * stance_weight(row["trust_tier"], row["published_at"]),
        reverse=True,
    )[:limit]

    matches = [
        SemanticCreatorMatch(
            creator_name=row["creator_name"],
            trust_tier=row["trust_tier"],
            stance=row["stance"],
            confidence=float(row["confidence"]),
            reason=row["reason"],
            matched_text=row["matched_text"],
            citation_id=row["citation_id"],
            source_url=row["source_url"],
            start_seconds=row["start_seconds"],
            published_at=row["published_at"].isoformat() if row["published_at"] else None,
            gameweek=row["gameweek"],
            age_days=age_days(row["published_at"]),
            recency=describe_recency(row["published_at"], row["gameweek"], planning),
            is_current_gameweek=is_current(row["gameweek"], planning),
        )
        for row in ranked
    ]
    return SemanticCreatorOpinions(
        query=query,
        matches=matches,
        total_matched=len(rows),
        returned=len(matches),
        planning_gameweek=planning,
    )


def make_search_creator_opinions_tool(
    conn: Connection, voyage_client: VoyageClient, team_id: int
) -> ToolSpec:
    return ToolSpec(
        name="search_creator_opinions",
        description=(
            "Semantic search over what trusted creators have said, for loose queries that "
            "aren't an exact player name -- e.g. a team's fixture run, a tactical theme, or a "
            "vague topic. Prefer get_creator_opinions when asking about one specific named "
            "player. Each result resolves to its Creator Stance's structured fields (creator, "
            "trust tier, stance, confidence) and carries a citation_id: pass it back on the "
            "matching evidence line in submit_recommendation or submit_plan so the user can "
            "click through to where it was said. Results are ranked by relevance scaled "
            "by recency and trust tier, and capped -- compare total_matched with returned "
            "to see whether you are looking at a slice. Each match carries a `recency` "
            "phrase giving its age and the gameweek it was published for: a match from an "
            "already-played gameweek is background, not current advice, and loses to a "
            "newer match that contradicts it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "a loose, natural-language search"}
            },
            "required": ["query"],
        },
        handler=lambda query: search_creator_opinions(conn, voyage_client, query, team_id),
    )
