from dataclasses import dataclass

from psycopg import Connection

from fpl_coach.ingestion.extraction import StanceExtractor
from fpl_coach.ingestion.grok_client import GrokClient
from fpl_coach.ingestion.voyage_client import VoyageClient, vector_literal
from fpl_coach.instincts import current_gameweek
from fpl_coach.tools.get_player import find_player

_MAX_CHUNK_CHARS = 2000


@dataclass(frozen=True)
class CreatorIngestionSummary:
    creators_processed: int
    content_ingested: int
    content_skipped: int
    stances_extracted: int
    chunks_embedded: int


def run_creator_ingestion(
    conn: Connection,
    grok_client: GrokClient,
    extractor: StanceExtractor,
    voyage_client: VoyageClient,
    team_id: int,
    creator_ids: list[int] | None = None,
) -> CreatorIngestionSummary:
    """Per creator: pulls recent posts via Grok's X Search (issue 06), stores
    each as creator_content (idempotent -- ON CONFLICT DO NOTHING on the
    UNIQUE (creator_id, source_url), since content accumulates across runs
    rather than getting replaced), then runs Layer-2 extraction on each newly
    -stored piece of content into creator_stances.

    creator_ids optionally scopes the run to specific creators -- omitted
    (the CLI's usage) processes every creator. Tests pass their own
    freshly-inserted creator id so a fake grok_client never gets called for
    real creators already in the (shared) database -- calling it for every
    creator regardless of scope previously wrote fake test URLs into a real
    creator's content on every test run.

    Embedding (issue 06 criterion 4, ADR-0005's vector facet) is driven off
    creator_content rows lacking a content_chunks row yet -- not off
    content_ingested this run -- so it backfills any pre-existing,
    not-yet-embedded content for the creators in scope. Scoped by the same
    creator_ids as everything else above: embedding unscoped (ANY creator
    lacking chunks) would mean a test's FakeVoyageClient writes fabricated
    vectors into a real creator's content_chunks rows, the same class of
    shared-DB pollution as issue 08's creator_ids fix.

    team_id (issue 10): each extracted Stance is tagged with team_id's
    current gameweek (NULL if no team ingestion has ever resolved one yet)
    so a later ingestion cycle can record its outcome. Taken as a parameter
    rather than importing MY_TEAM_ID here directly, so this ingestion module
    doesn't reach into the api layer -- the CLI passes MY_TEAM_ID."""
    with conn.cursor() as cur:
        if creator_ids is None:
            cur.execute("SELECT id, x_handle FROM creators")
        else:
            cur.execute("SELECT id, x_handle FROM creators WHERE id = ANY(%s)", (creator_ids,))
        creators = cur.fetchall()

    gameweek = current_gameweek(conn, team_id)
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
                        " stance, confidence, reason, gameweek) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            creator["id"],
                            content_id,
                            stance["player_name"],
                            matched.id if matched is not None else None,
                            stance["stance"],
                            stance["confidence"],
                            stance["reason"],
                            gameweek,
                        ),
                    )
                stances_extracted += 1

        conn.commit()

    chunks_embedded = _embed_pending_content(conn, voyage_client, [c["id"] for c in creators])

    return CreatorIngestionSummary(
        creators_processed=len(creators),
        content_ingested=content_ingested,
        content_skipped=content_skipped,
        stances_extracted=stances_extracted,
        chunks_embedded=chunks_embedded,
    )


def _embed_pending_content(
    conn: Connection, voyage_client: VoyageClient, creator_id_list: list[int]
) -> int:
    if not creator_id_list:
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, content_text FROM creator_content "
            "WHERE creator_id = ANY(%s) "
            "AND NOT EXISTS (SELECT 1 FROM content_chunks WHERE content_id = creator_content.id)",
            (creator_id_list,),
        )
        pending = cur.fetchall()

    chunks_embedded = 0
    for row in pending:
        chunks = _chunk_text(row["content_text"])
        if not chunks:
            continue
        embeddings = voyage_client.embed(chunks)
        with conn.cursor() as cur:
            for chunk_text, embedding in zip(chunks, embeddings):
                cur.execute(
                    "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                    "VALUES (%s, %s, %s::vector)",
                    (row["id"], chunk_text, vector_literal(embedding)),
                )
        chunks_embedded += len(chunks)

    if pending:
        conn.commit()
    return chunks_embedded


def _chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Grok's paraphrases (creator_content.content_text) are short, so this
    almost always yields a single chunk -- a flat character-length split
    guards against the rare long post rather than doing sentence-aware
    chunking that has no current input to exercise it."""
    text = text.strip()
    if not text:
        return []
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
