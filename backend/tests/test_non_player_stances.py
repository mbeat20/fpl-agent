"""Issue 16: a Stance that names no player must be storable, retrievable, and
incapable of becoming a Recommendation's pick.

The last part is the one worth a test with teeth. ADR-0004's rule is that
`submit_recommendation`'s pick_player_id is an enum built only from players the
model has actually seen tool data for -- so widening the stance vocabulary must
not open a side door where "wildcard" or "Arsenal" arrives as a pick.
"""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.models import (
    CreatorOpinions,
    Player,
    PriceChangePredictions,
    SemanticCreatorMatch,
    SemanticCreatorOpinions,
)
from fpl_coach.reasoning.claude import ClaudeReasoning
from fpl_coach.tools.get_creator_opinions import get_creator_opinions

TEST_HANDLE = "non_player_stance_creator"

# Issue 18: these tools now resolve a planning gameweek to label staleness
# against, so they take a team id. Deliberately NOT MY_TEAM_ID -- nothing here
# should touch the real team's row.
TEST_TEAM_ID = 90218


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def creator_with_non_player_stances():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Non Player Stance Creator", TEST_HANDLE, "medium"),
            )
            creator_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text, platform) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (creator_id, "https://youtu.be/nonplayer", "[00:10] wildcard around gw8", "youtube"),
            )
            content_id = cur.fetchone()["id"]
            cur.executemany(
                "INSERT INTO creator_stances (creator_id, source_content_id, player_name, subject, "
                "stance, confidence, reason, start_seconds) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (creator_id, content_id, None, "wildcard", "chip_timing", 0.7, "Around GW8.", 10),
                    (creator_id, content_id, None, "Arsenal", "fixture_view", 0.8, "Best run.", 40),
                    (creator_id, content_id, "Bukayo Saka", None, "buy", 0.6, "Fixtures.", 60),
                ],
            )
        conn.commit()
    yield creator_id
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))


def test_a_non_player_stance_is_stored_rather_than_discarded(creator_with_non_player_stances):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stance, player_name, subject, start_seconds FROM creator_stances "
                "WHERE creator_id = %s AND player_name IS NULL ORDER BY stance",
                (creator_with_non_player_stances,),
            )
            rows = cur.fetchall()

    assert [r["stance"] for r in rows] == ["chip_timing", "fixture_view"]
    assert [r["subject"] for r in rows] == ["wildcard", "Arsenal"]
    assert all(r["start_seconds"] is not None for r in rows)


def test_a_player_lookup_does_not_return_non_player_stances(creator_with_non_player_stances):
    """get_creator_opinions filters on `player_name ILIKE`, and NULL never
    matches -- so asking about Saka cannot surface a chip-timing opinion."""
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Saka", TEST_TEAM_ID)

    assert isinstance(opinions, CreatorOpinions)
    ours = [s for s in opinions.stances if s.creator_name == "Non Player Stance Creator"]
    assert len(ours) == 1
    assert ours[0].stance == "buy"


def test_a_creator_opinion_result_can_never_contribute_a_pick():
    """The structural guarantee behind ADR-0004: _record_known_players only
    learns players from tool results that actually carry a Player, and no
    creator-opinion result does. If a future change adds a player-shaped
    field to one of these, this test is the thing that should fail.
    """
    known: dict = {}

    ClaudeReasoning._record_known_players(
        CreatorOpinions(player_name="wildcard", stances=[]), known
    )
    ClaudeReasoning._record_known_players(
        SemanticCreatorOpinions(
            query="when to wildcard",
            matches=[
                SemanticCreatorMatch(
                    creator_name="c",
                    trust_tier="high",
                    stance="chip_timing",
                    confidence=0.7,
                    reason="Around GW8.",
                    matched_text="[00:10] wildcard around gw8",
                )
            ],
        ),
        known,
    )

    assert known == {}

    # Sanity check that the recorder is not simply inert -- a real Player
    # result does register, so the emptiness above means something.
    ClaudeReasoning._record_known_players(
        Player(id=1, name="Bukayo Saka", position="MID", club="Arsenal", price=10.0), known
    )
    assert list(known) == [1]

    ClaudeReasoning._record_known_players(
        PriceChangePredictions(rising=[], falling=[]), known
    )
    assert list(known) == [1]
