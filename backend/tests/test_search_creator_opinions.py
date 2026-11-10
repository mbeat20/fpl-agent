import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION, vector_literal
from fpl_coach.tools.search_creator_opinions import (
    make_search_creator_opinions_tool,
    search_creator_opinions,
)

TEST_HANDLE = "search_test_creator"


def _unit_vector(axis: int) -> list[float]:
    """A near-orthogonal test vector: cosine distance 0 to itself, 1 to any
    other axis's vector -- provable nearest-neighbour ordering with zero API
    calls, same idea as _FakeGrokClient/FakeStanceExtractor's canned fakes."""
    v = [0.0] * EMBEDDING_DIMENSION
    v[axis] = 1.0
    return v


class _FakeVoyageClient:
    def __init__(self, query_vector: list[float]) -> None:
        self._query_vector = query_vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._query_vector for _ in texts]

# Issue 18: these tools now resolve a planning gameweek to label staleness
# against, so they take a team id. Deliberately NOT MY_TEAM_ID -- nothing here
# should touch the real team's row.
TEST_TEAM_ID = 90218


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def seeded_creator_with_stances():
    """Two content rows (each embedded on a different, near-orthogonal
    axis) each with one Stance, plus a third content row with a chunk but
    NO Stance -- proves stance-less chunks are dropped rather than
    surfaced as bare text (ticket 09's AC: must resolve to structured
    fields)."""
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) RETURNING id",
                ("Search Test Creator", TEST_HANDLE, "high"),
            )
            creator_id = cur.fetchone()["id"]

            content_ids = []
            for i, text in enumerate(["Salah has great fixtures.", "Newcastle's run is brutal."]):
                cur.execute(
                    "INSERT INTO creator_content (creator_id, source_url, content_text) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (creator_id, f"https://x.com/{TEST_HANDLE}/status/{i}", text),
                )
                content_id = cur.fetchone()["id"]
                content_ids.append(content_id)
                cur.execute(
                    "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                    "VALUES (%s, %s, %s::vector)",
                    (content_id, text, vector_literal(_unit_vector(i))),
                )

            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (creator_id, content_ids[0], "Mohamed Salah", "buy", 0.8, "Fixtures."),
            )
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (creator_id, content_ids[1], "Alexander Isak", "sell", 0.6, "Fixture run."),
            )

            # Stance-less content: has a chunk, no creator_stances row.
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (creator_id, f"https://x.com/{TEST_HANDLE}/status/stanceless", "General banter."),
            )
            stanceless_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                "VALUES (%s, %s, %s::vector)",
                (stanceless_id, "General banter.", vector_literal(_unit_vector(2))),
            )
        conn.commit()
    yield creator_id
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))  # cascades content/chunks/stances


def test_nearest_chunk_ranks_first(seeded_creator_with_stances):
    """A query embedding matching axis 0's vector exactly should surface the
    Salah stance ahead of the Newcastle one -- proves ordering by embedding
    similarity, not insertion order or trust tier."""
    with _conn() as conn:
        result = search_creator_opinions(
            conn,
            _FakeVoyageClient(_unit_vector(0)),
            "anything, query text doesn't matter here",
            TEST_TEAM_ID,
        )

    assert len(result.matches) >= 1
    assert result.matches[0].stance == "buy"
    assert "Salah" in result.matches[0].reason or result.matches[0].reason == "Fixtures."


def test_a_query_with_no_exact_player_name_still_returns_a_relevant_stance(
    seeded_creator_with_stances,
):
    """Ticket 09 AC2: a loose query (no exact player name) about a team's
    fixture run resolves via embedding proximity, not text matching --
    axis 1's vector was seeded against Newcastle's fixture-run content."""
    with _conn() as conn:
        result = search_creator_opinions(
            conn, _FakeVoyageClient(_unit_vector(1)), "Newcastle's fixture run", TEST_TEAM_ID
        )

    assert len(result.matches) >= 1
    assert result.matches[0].stance == "sell"
    assert result.matches[0].creator_name == "Search Test Creator"
    assert result.matches[0].trust_tier == "high"


def test_stanceless_content_is_never_surfaced(seeded_creator_with_stances):
    """A chunk whose content_id has no creator_stances row (ADR-0005: a
    content row can yield zero Stances) must be dropped by the join, not
    returned as bare unresolved text."""
    with _conn() as conn:
        result = search_creator_opinions(
            conn, _FakeVoyageClient(_unit_vector(2)), "banter", TEST_TEAM_ID
        )

    assert all(m.matched_text != "General banter." for m in result.matches)


def test_tool_handler_is_wired_to_search_creator_opinions(seeded_creator_with_stances):
    with _conn() as conn:
        tool = make_search_creator_opinions_tool(
            conn, _FakeVoyageClient(_unit_vector(0)), TEST_TEAM_ID
        )
        result = tool.handler(query="Salah fixtures")

    assert result.query == "Salah fixtures"
    assert len(result.matches) >= 1
