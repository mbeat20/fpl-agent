import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.ingestion.creator_pipeline import run_creator_ingestion
from fpl_coach.ingestion.extraction import FakeStanceExtractor
from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION

TEST_HANDLE = "pipeline_test_creator"
# Issue 10: an unused team id with no gameweek_state row, so current_gameweek
# resolves to None -- fine for tests not specifically about gameweek tagging
# (a plain SELECT lookup, no FK to violate).
NO_GAMEWEEK_TEAM_ID = -1

POSTS = [
    {"url": "https://x.com/pipeline_test_creator/status/1", "text": "Buy Salah, great fixtures."},
    {"url": "https://x.com/pipeline_test_creator/status/2", "text": "Captain Haaland this week."},
]

STANCES = [
    {"player_name": "Mohamed Salah", "stance": "buy", "confidence": 0.7, "reason": "Fixtures."},
]


class _FakeGrokClient:
    def __init__(self, posts: list[dict]) -> None:
        self._posts = posts
        self.calls: list[str] = []

    def search_creator_posts(self, x_handle: str, limit: int = 5) -> list[dict]:
        self.calls.append(x_handle)
        return self._posts


class _FakeVoyageClient:
    """Fakes at the same seam as _FakeGrokClient/FakeStanceExtractor --
    deterministic vectors (index-based, not random) so embedding call counts
    and content_chunks row counts are provable without a real API call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(i % 7) / 7] * EMBEDDING_DIMENSION for i in range(len(texts))]


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def test_creator():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Pipeline Test Creator", TEST_HANDLE, "medium"),
            )
            creator_id = cur.fetchone()["id"]
        conn.commit()
    yield creator_id
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))  # cascades content/stances


def test_ingests_content_and_extracts_stances(test_creator):
    with _conn() as conn:
        summary = run_creator_ingestion(
            conn,
            _FakeGrokClient(POSTS),
            FakeStanceExtractor(STANCES),
            _FakeVoyageClient(),
            NO_GAMEWEEK_TEAM_ID,
            creator_ids=[test_creator],
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM creator_content WHERE creator_id = %s", (test_creator,))
            content_count = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM creator_stances WHERE creator_id = %s", (test_creator,))
            stance_count = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM content_chunks ch "
                "JOIN creator_content cc ON cc.id = ch.content_id "
                "WHERE cc.creator_id = %s",
                (test_creator,),
            )
            chunk_count = cur.fetchone()["n"]

    assert summary.creators_processed == 1
    assert summary.content_ingested == 2
    assert summary.content_skipped == 0
    # One Stance per post (STANCES has 1 item), applied to each of the 2 posts.
    assert summary.stances_extracted == 2
    assert content_count == 2
    assert stance_count == 2
    # One chunk per post -- Grok's paraphrases are short, well under _MAX_CHUNK_CHARS.
    assert summary.chunks_embedded == 2
    assert chunk_count == 2


def test_re_running_ingestion_is_idempotent_and_does_not_duplicate(test_creator):
    """Pitfall #2 (ticket 06 design notes): creator content accumulates
    rather than getting replaced, so a naive re-run must not duplicate
    content or re-extract stances for posts already seen. Embedding is
    driven off content lacking chunks (issue 06 criterion 4), so it must
    stay idempotent the same way."""
    grok_client = _FakeGrokClient(POSTS)
    extractor = FakeStanceExtractor(STANCES)
    voyage_client = _FakeVoyageClient()

    with _conn() as conn:
        run_creator_ingestion(
            conn, grok_client, extractor, voyage_client, NO_GAMEWEEK_TEAM_ID, creator_ids=[test_creator]
        )
        second_summary = run_creator_ingestion(
            conn, grok_client, extractor, voyage_client, NO_GAMEWEEK_TEAM_ID, creator_ids=[test_creator]
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM creator_content WHERE creator_id = %s", (test_creator,))
            content_count = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM creator_stances WHERE creator_id = %s", (test_creator,))
            stance_count = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM content_chunks ch "
                "JOIN creator_content cc ON cc.id = ch.content_id "
                "WHERE cc.creator_id = %s",
                (test_creator,),
            )
            chunk_count = cur.fetchone()["n"]

    assert second_summary.content_ingested == 0
    assert second_summary.content_skipped == 2
    assert second_summary.stances_extracted == 0
    assert second_summary.chunks_embedded == 0
    assert content_count == 2  # not 4
    assert stance_count == 2  # not 4
    assert chunk_count == 2  # not 4


def test_stances_are_matched_to_an_fpl_player_id_when_a_match_exists(test_creator):
    """Best-effort match against fpl_players by name, same convention as
    players.fpl_element_id -- NULL when unmatched (no FK, see schema.sql)."""
    with _conn() as conn:
        run_creator_ingestion(
            conn,
            _FakeGrokClient(POSTS[:1]),
            FakeStanceExtractor(STANCES),
            _FakeVoyageClient(),
            NO_GAMEWEEK_TEAM_ID,
            creator_ids=[test_creator],
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT fpl_player_id FROM creator_stances WHERE creator_id = %s", (test_creator,)
            )
            rows = cur.fetchall()

    # No fpl_players seeded in this test's isolation -- must not crash, and
    # must land on NULL (unmatched) rather than a wrong id.
    assert all(row["fpl_player_id"] is None for row in rows)


def test_stances_are_tagged_with_the_team_s_current_gameweek(test_creator, seeded_team):
    """Issue 10: a Stance is tagged with team_id's current gameweek at
    extraction time, so a later ingestion cycle can find it once it's due
    for an outcome. Uses seeded_team (MY_TEAM_ID) since gameweek_state is
    keyed by team_id, not a global fact."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 9),
        )
        run_creator_ingestion(
            conn,
            _FakeGrokClient(POSTS[:1]),
            FakeStanceExtractor(STANCES),
            _FakeVoyageClient(),
            seeded_team,
            creator_ids=[test_creator],
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek FROM creator_stances WHERE creator_id = %s", (test_creator,)
            )
            rows = cur.fetchall()

    assert rows and all(row["gameweek"] == 9 for row in rows)


def test_stances_have_no_gameweek_when_team_ingestion_has_never_run(test_creator):
    """NULL, not an error -- creator ingestion must not require team
    ingestion to have run first."""
    with _conn() as conn:
        run_creator_ingestion(
            conn,
            _FakeGrokClient(POSTS[:1]),
            FakeStanceExtractor(STANCES),
            _FakeVoyageClient(),
            NO_GAMEWEEK_TEAM_ID,
            creator_ids=[test_creator],
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek FROM creator_stances WHERE creator_id = %s", (test_creator,)
            )
            rows = cur.fetchall()

    assert rows and all(row["gameweek"] is None for row in rows)
