from psycopg import Connection

from fpl_coach.ingestion.voyage_client import VoyageClient, vector_literal
from fpl_coach.models import SemanticCreatorMatch, SemanticCreatorOpinions
from fpl_coach.reasoning.base import ToolSpec

# Two-stage: narrow to the nearest chunks first, then join out to Stances.
# Joining chunks -> creator_stances directly and taking the top `limit` rows
# would let one verbose post's multiple Stances crowd out every other
# creator -- CTE-then-join keeps the candidate pool spread across posts
# (ADR-0005: many-Stances-to-one-source, not 1:1). A chunk with no extracted
# Stance (a content row can yield zero, per ADR-0005) is simply dropped by
# the join -- ticket 09's AC requires resolving back to structured fields,
# not returning raw text alone.
_SEARCH_SQL = """
WITH nearest_chunks AS (
    SELECT content_id, chunk_text, embedding <=> %(query_embedding)s::vector AS distance
    FROM content_chunks
    ORDER BY distance
    LIMIT %(chunk_pool)s
)
SELECT c.name AS creator_name, c.trust_tier, s.stance, s.confidence, s.reason,
       nc.chunk_text AS matched_text, nc.distance
FROM nearest_chunks nc
JOIN creator_stances s ON s.source_content_id = nc.content_id
JOIN creators c ON c.id = s.creator_id
ORDER BY nc.distance
LIMIT %(limit)s
"""


def search_creator_opinions(
    conn: Connection, voyage_client: VoyageClient, query: str, limit: int = 5
) -> SemanticCreatorOpinions:
    embedding = voyage_client.embed([query])[0]
    with conn.cursor() as cur:
        cur.execute(
            _SEARCH_SQL,
            {
                "query_embedding": vector_literal(embedding),
                "chunk_pool": max(limit * 3, 10),
                "limit": limit,
            },
        )
        rows = cur.fetchall()

    matches = [
        SemanticCreatorMatch(
            creator_name=row["creator_name"],
            trust_tier=row["trust_tier"],
            stance=row["stance"],
            confidence=float(row["confidence"]),
            reason=row["reason"],
            matched_text=row["matched_text"],
        )
        for row in rows
    ]
    return SemanticCreatorOpinions(query=query, matches=matches)


def make_search_creator_opinions_tool(conn: Connection, voyage_client: VoyageClient) -> ToolSpec:
    return ToolSpec(
        name="search_creator_opinions",
        description=(
            "Semantic search over what trusted creators have said, for loose queries that "
            "aren't an exact player name -- e.g. a team's fixture run, a tactical theme, or a "
            "vague topic. Prefer get_creator_opinions when asking about one specific named "
            "player. Each result resolves to its Creator Stance's structured fields (creator, "
            "trust tier, stance, confidence)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "a loose, natural-language search"}
            },
            "required": ["query"],
        },
        handler=lambda query: search_creator_opinions(conn, voyage_client, query),
    )
