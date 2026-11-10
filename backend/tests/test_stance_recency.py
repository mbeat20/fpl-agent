"""Issue 18: a Creator Stance stops being timeless.

The failure this guards against is the one the user actually hit: opinions
from an already-played gameweek arriving in the same undifferentiated list as
opinions about the gameweek being planned, so the reasoning layer answered a
GW2 question with GW1 evidence.

Seeds its own creators and its own team, never MY_TEAM_ID -- conftest's
seeded_team fixture deletes the real team row and cascade-wipes real state,
and nothing here needs to go near it.
"""

import itertools
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.ingestion.creator_pipeline import _x_published_at
from fpl_coach.stance_ranking import (
    HALF_LIFE_DAYS,
    describe_recency,
    recency_weight,
    stance_weight,
)
from fpl_coach.tools.get_creator_opinions import get_creator_opinions

TEST_HANDLES = ("recency_a", "recency_b", "recency_loud")
TEST_TEAM_ID = 90218

# Deliberately far beyond any real gameweek, and persisted in gameweek_state,
# so planning_gameweek resolves to it via max(persisted, upcoming) regardless
# of where the real season has got to. Without this the tests would silently
# change meaning every time the live fixtures table moved on.
PLANNING_GW = 30

NOW = datetime.now(timezone.utc)


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = ANY(%s)", (list(TEST_HANDLES),))
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))


def _creator(cur, name, handle, tier):
    cur.execute(
        "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) RETURNING id",
        (name, handle, tier),
    )
    return cur.fetchone()["id"]


_url_counter = itertools.count(1)


def _stance(cur, creator_id, player, stance, published_at, gameweek, confidence=0.7, url=None):
    cur.execute(
        "INSERT INTO creator_content (creator_id, source_url, content_text, published_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (creator_id, url or f"https://x.com/t/status/{next(_url_counter)}",
         f"{stance} {player}", published_at),
    )
    content_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO creator_stances "
        "(creator_id, source_content_id, player_name, stance, confidence, reason, gameweek) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (creator_id, content_id, player, stance, confidence, f"because {stance}", gameweek),
    )


@pytest.fixture
def contradictory_pair():
    """The ticket's required test shape: the same creator, the same player,
    an old 'buy' and a new 'sell'. If recency does nothing, these are
    indistinguishable and the model has no basis to prefer either."""
    _cleanup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Recency Test Team")
            )
            cur.execute(
                "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
                (TEST_TEAM_ID, PLANNING_GW),
            )
            creator = _creator(cur, "Recency Creator A", "recency_a", "high")
            _stance(cur, creator, "Test Striker", "buy", NOW - timedelta(days=8), PLANNING_GW - 1)
            _stance(cur, creator, "Test Striker", "sell", NOW - timedelta(hours=6), PLANNING_GW)
        conn.commit()
    yield
    _cleanup()


@pytest.fixture
def only_stale_opinions():
    _cleanup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Recency Test Team")
            )
            cur.execute(
                "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
                (TEST_TEAM_ID, PLANNING_GW),
            )
            creator = _creator(cur, "Recency Creator A", "recency_a", "high")
            _stance(cur, creator, "Forgotten Man", "buy", NOW - timedelta(days=60), PLANNING_GW - 6)
            _stance(cur, creator, "Forgotten Man", "hold", NOW - timedelta(days=45), PLANNING_GW - 5)
        conn.commit()
    yield
    _cleanup()


@pytest.fixture
def one_loud_creator():
    _cleanup()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Recency Test Team")
            )
            cur.execute(
                "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
                (TEST_TEAM_ID, PLANNING_GW),
            )
            loud = _creator(cur, "Very Loud Creator", "recency_loud", "high")
            quiet = _creator(cur, "Quiet Creator", "recency_b", "high")
            for hours in range(1, 8):
                _stance(
                    cur, loud, "Contested Player", "buy",
                    NOW - timedelta(hours=hours), PLANNING_GW,
                )
            _stance(
                cur, quiet, "Contested Player", "sell",
                NOW - timedelta(hours=20), PLANNING_GW,
            )
        conn.commit()
    yield
    _cleanup()


# --- the behaviour the ticket exists for -------------------------------------


def test_a_newer_stance_outranks_an_older_contradictory_one(contradictory_pair):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Test Striker", TEST_TEAM_ID)

    assert [s.stance for s in opinions.stances] == ["sell", "buy"]


def test_an_old_and_a_new_stance_are_distinguishable_in_the_model_s_input(contradictory_pair):
    """Ordering alone is not enough -- the model has to be *told* which is
    stale, in words, or it cannot explain or justify preferring the newer."""
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Test Striker", TEST_TEAM_ID)

    newest, oldest = opinions.stances
    assert newest.is_current_gameweek is True
    assert oldest.is_current_gameweek is False
    assert f"GW{PLANNING_GW}" in newest.recency
    assert "already played" in oldest.recency
    assert "predates" in oldest.recency


def test_the_result_says_how_much_it_is_not_showing(contradictory_pair):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Test Striker", TEST_TEAM_ID, limit=1)

    assert opinions.total_matched == 2
    assert opinions.returned == 1
    assert opinions.planning_gameweek == PLANNING_GW


def test_no_recent_opinion_is_a_distinct_answer_not_an_empty_list(only_stale_opinions):
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Forgotten Man", TEST_TEAM_ID)

    assert opinions.stances == []
    assert opinions.older_not_shown == 2
    assert opinions.note is not None
    assert "No trusted creator" in opinions.note
    # The distinction that matters: silence is not endorsement.
    assert "not as agreement" in opinions.note


def test_stale_stances_are_hidden_but_never_deleted(only_stale_opinions):
    with _conn() as conn:
        get_creator_opinions(conn, "Forgotten Man", TEST_TEAM_ID)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM creator_stances WHERE player_name = %s",
                ("Forgotten Man",),
            )
            assert cur.fetchone()["n"] == 2


def test_one_prolific_creator_cannot_take_every_slot(one_loud_creator):
    """Seven fresh stances from one account and one from another. Without a
    per-creator cap the quiet creator never appears, and a single voice reads
    as consensus."""
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Contested Player", TEST_TEAM_ID)

    names = [s.creator_name for s in opinions.stances]
    assert "Quiet Creator" in names
    assert names.count("Very Loud Creator") <= 2


# --- the ranking itself ------------------------------------------------------


def test_recency_weight_halves_every_half_life():
    fresh = recency_weight(NOW, now=NOW)
    one_half_life = recency_weight(NOW - timedelta(days=HALF_LIFE_DAYS), now=NOW)
    two_half_lives = recency_weight(NOW - timedelta(days=HALF_LIFE_DAYS * 2), now=NOW)

    assert fresh == pytest.approx(1.0, abs=1e-6)
    assert one_half_life == pytest.approx(0.5, abs=1e-6)
    assert two_half_lives == pytest.approx(0.25, abs=1e-6)


def test_trust_tier_changes_weight_when_recency_is_equal():
    high = stance_weight("high", NOW, now=NOW)
    medium = stance_weight("medium", NOW, now=NOW)
    low = stance_weight("low", NOW, now=NOW)

    assert high > medium > low


def test_a_fresh_medium_creator_outranks_a_stale_high_one():
    """ADR-0001's tiers are a real signal, but they are not supposed to
    outweigh a whole gameweek of staleness."""
    fresh_medium = stance_weight("medium", NOW - timedelta(hours=2), now=NOW)
    stale_high = stance_weight("high", NOW - timedelta(days=10), now=NOW)

    assert fresh_medium > stale_high


def test_an_undated_stance_ranks_below_a_recent_one_without_vanishing():
    undated = stance_weight("high", None, now=NOW)
    recent = stance_weight("high", NOW - timedelta(days=1), now=NOW)

    assert 0 < undated < recent


def test_describe_recency_names_the_gameweek_and_the_staleness():
    current = describe_recency(NOW - timedelta(hours=3), 30, 30, now=NOW)
    previous = describe_recency(NOW - timedelta(days=8), 29, 30, now=NOW)
    ancient = describe_recency(NOW - timedelta(days=40), 25, 30, now=NOW)

    assert "being planned" in current
    assert "already played" in previous
    assert "5 gameweeks stale" in ancient


def test_describe_recency_survives_an_unknown_date():
    assert "date unknown" in describe_recency(None, 30, 30, now=NOW)


# --- the date the ranking depends on -----------------------------------------


def test_x_publish_time_is_decoded_from_the_post_id():
    """Grok's X Search returns no date, so without this every X Stance is
    undated and the ranking above has nothing to work with."""
    published = _x_published_at("https://x.com/FPLGeneral/status/2029301123081757112")

    assert published is not None
    assert published.year == 2026
    assert published.month == 3
    assert published.day == 4


def test_a_url_with_no_post_id_is_undated_rather_than_guessed():
    assert _x_published_at("https://example.com/some-article") is None


def test_a_pre_snowflake_id_is_rejected_rather_than_decoded_to_nonsense():
    assert _x_published_at("https://x.com/someone/status/12345") is None
