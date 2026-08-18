from dataclasses import dataclass

from psycopg import Connection

from fpl_coach.ingestion.extraction import StanceExtractor
from fpl_coach.ingestion.grok_client import GrokClient
from fpl_coach.tools.get_player import find_player


@dataclass(frozen=True)
class CreatorIngestionSummary:
    creators_processed: int
    content_ingested: int
    content_skipped: int
    stances_extracted: int


def run_creator_ingestion(
    conn: Connection, grok_client: GrokClient, extractor: StanceExtractor
) -> CreatorIngestionSummary:
    """Per creator: pulls recent posts via Grok's X Search (issue 06), stores
    each as creator_content (idempotent -- ON CONFLICT DO NOTHING on the
    UNIQUE (creator_id, source_url), since content accumulates across runs
    rather than getting replaced), then runs Layer-2 extraction on each newly
    -stored piece of content into creator_stances."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, x_handle FROM creators")
        creators = cur.fetchall()

    content_ingested = 0
    content_skipped = 0
    stances_extracted = 0

    for creator in creators:
        posts = grok_client.search_creator_posts(creator["x_handle"])
        for post in posts:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO creator_content (creator_id, source_url, content_text) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (creator_id, source_url) DO NOTHING "
                    "RETURNING id",
                    (creator["id"], post["url"], post["text"]),
                )
                row = cur.fetchone()

            if row is None:
                content_skipped += 1
                continue
            content_ingested += 1
            content_id = row["id"]

            for stance in extractor.extract(post["text"]):
                matched = find_player(conn, stance["player_name"])
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO creator_stances "
                        "(creator_id, source_content_id, player_name, fpl_player_id, "
                        " stance, confidence, reason) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            creator["id"],
                            content_id,
                            stance["player_name"],
                            matched.id if matched is not None else None,
                            stance["stance"],
                            stance["confidence"],
                            stance["reason"],
                        ),
                    )
                stances_extracted += 1

        conn.commit()

    return CreatorIngestionSummary(
        creators_processed=len(creators),
        content_ingested=content_ingested,
        content_skipped=content_skipped,
        stances_extracted=stances_extracted,
    )
