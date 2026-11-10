"""Issue 16: the YouTube/transcript leg of creator ingestion.

Kept separate from test_creator_pipeline.py, which covers the ticket-06 X leg
and must keep passing untouched -- proving the new arguments are genuinely
optional rather than a rewrite of the existing path.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.creators import add_creator_source
from fpl_coach.ingestion.creator_pipeline import _chunk_content, run_creator_ingestion
from fpl_coach.ingestion.extraction import FakeStanceExtractor
from fpl_coach.ingestion.transcripts import FakeTranscriptSource
from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION

TEST_NAME = "YouTube Pipeline Test Creator"
CHANNEL_ID = "UCtestchannelidtestchannel"
NO_GAMEWEEK_TEAM_ID = -1

def days_ago(days: float) -> str:
    """A publish date relative to the run, as the RSS feed would give it.

    These fixtures were originally written as fixed dates, which made them a
    time bomb: `_MAX_VIDEO_AGE_DAYS` is measured against `now`, so a video
    written to be "recent enough to ingest" silently became "too old" once the
    wall clock passed the cutoff, and seven tests that say nothing about the
    age rule failed together. A test that is about the cutoff should say so;
    the rest should be unable to trip over it. Anything deliberately ancient
    stays a literal date -- 2010 is past every cutoff there will ever be.
    """
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


VIDEOS = [
    {
        "video_id": "vid_new",
        "title": "FPL Gameweek 2 Preview",
        "published_at": days_ago(2),
        "url": "https://www.youtube.com/watch?v=vid_new",
    },
    {
        "video_id": "vid_old",
        "title": "FPL Gameweek 1 Preview",
        "published_at": days_ago(9),
        "url": "https://www.youtube.com/watch?v=vid_old",
    },
]

TRANSCRIPTS = {
    "vid_new": [("welcome back everyone", 0.0, 5.0), ("I am buying Wissa this week", 743.0, 5.0)],
    "vid_old": [("last week thoughts", 0.0, 5.0)],
}

# A transcript legitimately yields several stances from one call, including one
# that names no player at all -- the claim shape ticket 06's schema threw away.
TRANSCRIPT_STANCES = [
    {
        "player_name": "Yoane Wissa",
        "stance": "buy",
        "confidence": 0.8,
        "reason": "Great fixtures.",
        "start_seconds": 743,
    },
    {
        "subject": "wildcard",
        "stance": "chip_timing",
        "confidence": 0.6,
        "reason": "Around GW8.",
        "start_seconds": 900,
    },
    {
        "subject": "Arsenal",
        "stance": "fixture_view",
        "confidence": 0.7,
        "reason": "Best run in the game from GW6.",
    },
]


class _FakeYouTubeClient:
    def __init__(self, videos=None):
        self._videos = videos if videos is not None else VIDEOS
        self.calls: list[str] = []

    def list_recent_videos(self, channel_id: str) -> list[dict]:
        self.calls.append(channel_id)
        return self._videos


class _UnusedGrokClient:
    """The creator under test has no x_handle, so Grok must never be called.
    Raising rather than returning [] makes an accidental call a failure."""

    def search_creator_posts(self, x_handle: str, limit: int = 5) -> list[dict]:
        raise AssertionError(f"Grok called for a YouTube-only creator: {x_handle!r}")


class _FakeVoyageClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(i % 7) / 7] * EMBEDDING_DIMENSION for i in range(len(texts))]


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture
def youtube_creator():
    """A creator with NO x_handle -- the ticket-16 case that ticket 06's
    NOT NULL column made impossible."""
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE name = %s", (TEST_NAME,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, NULL, %s) "
                "RETURNING id",
                (TEST_NAME, "medium"),
            )
            creator_id = cur.fetchone()["id"]
        conn.commit()
        add_creator_source(conn, creator_id, "youtube", CHANNEL_ID, "testhandle")
    yield creator_id
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))


def _run(conn, creator_id, transcript_source=None, youtube_client=None, extractor=None, **kwargs):
    return run_creator_ingestion(
        conn,
        _UnusedGrokClient(),
        extractor or FakeStanceExtractor(TRANSCRIPT_STANCES),
        _FakeVoyageClient(),
        NO_GAMEWEEK_TEAM_ID,
        creator_ids=[creator_id],
        youtube_client=youtube_client or _FakeYouTubeClient(),
        transcript_source=transcript_source or FakeTranscriptSource(TRANSCRIPTS),
        **kwargs,
    )


def test_a_transcript_is_stored_with_platform_published_at_title_and_duration(youtube_creator):
    with _conn() as conn:
        summary = _run(conn, youtube_creator)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_url, platform, published_at, title, duration_seconds, content_text "
                "FROM creator_content WHERE creator_id = %s ORDER BY source_url",
                (youtube_creator,),
            )
            rows = cur.fetchall()

    assert summary.videos_ingested == 2
    assert summary.youtube_channels_checked == 1
    assert {r["source_url"] for r in rows} == {v["url"] for v in VIDEOS}
    assert all(r["platform"] == "youtube" for r in rows)
    # published_at is the column ticket 18's recency ranking depends on -- it
    # is the whole reason fetched_at was not enough.
    assert all(r["published_at"] is not None for r in rows)
    new = next(r for r in rows if r["source_url"].endswith("vid_new"))
    assert new["title"] == "FPL Gameweek 2 Preview"
    assert new["duration_seconds"] == 748
    # Timestamps live in the stored text so they stay recoverable during the
    # embedding backfill, which only ever reads content_text.
    assert "[12:23]" in new["content_text"]


def test_extraction_runs_once_per_transcript_not_once_per_chunk(youtube_creator):
    extractor = FakeStanceExtractor(TRANSCRIPT_STANCES)

    with _conn() as conn:
        _run(conn, youtube_creator, extractor=extractor)

    # Two videos, two extraction calls -- not one per chunk, and not one per
    # snippet. Chunks exist for pgvector retrieval, not for extraction.
    assert len(extractor.calls) == 2
    assert all("[00:00]" in call for call in extractor.calls)


def test_one_video_yields_several_stances_including_one_that_names_no_player(youtube_creator):
    with _conn() as conn:
        summary = _run(conn, youtube_creator)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT player_name, subject, stance, start_seconds FROM creator_stances "
                "WHERE creator_id = %s ORDER BY stance, subject NULLS FIRST",
                (youtube_creator,),
            )
            stances = cur.fetchall()

    assert summary.transcript_stances_extracted == 6  # 3 stances x 2 videos
    by_stance = {s["stance"] for s in stances}
    assert {"buy", "chip_timing", "fixture_view"} == by_stance

    chip = next(s for s in stances if s["stance"] == "chip_timing")
    assert chip["player_name"] is None
    assert chip["subject"] == "wildcard"

    fixture = next(s for s in stances if s["stance"] == "fixture_view")
    assert fixture["subject"] == "Arsenal"

    buy = next(s for s in stances if s["stance"] == "buy")
    assert buy["player_name"] == "Yoane Wissa"
    assert buy["start_seconds"] == 743


def test_a_stance_naming_neither_a_player_nor_a_subject_is_dropped(youtube_creator):
    """An unattributable opinion is worse than no opinion: ticket 19 would
    count it in a consensus it cannot be traced back to."""
    unattributable = [{"stance": "buy", "confidence": 0.9, "reason": "trust me"}]

    with _conn() as conn:
        summary = _run(conn, youtube_creator, extractor=FakeStanceExtractor(unattributable))

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM creator_stances WHERE creator_id = %s", (youtube_creator,)
            )
            count = cur.fetchone()["n"]

    assert summary.transcript_stances_extracted == 0
    assert count == 0


def test_chunks_carry_the_start_seconds_they_begin_at(youtube_creator):
    with _conn() as conn:
        summary = _run(conn, youtube_creator)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT ch.start_seconds, ch.chunk_text FROM content_chunks ch "
                "JOIN creator_content cc ON cc.id = ch.content_id "
                "WHERE cc.creator_id = %s AND cc.source_url LIKE %s",
                (youtube_creator, "%vid_new"),
            )
            chunks = cur.fetchall()

    # chunks_embedded counts both videos; this query is scoped to one of them.
    assert summary.chunks_embedded >= len(chunks) > 0
    assert chunks[0]["start_seconds"] == 0
    # A deep link is start_seconds away, not a whole hour-long video away.
    assert "[00:00]" not in chunks[0]["chunk_text"]


def test_re_running_ingests_and_extracts_nothing_and_spends_no_transcript_fetch(youtube_creator):
    """The fetch budget is the scarce resource -- a video already in the
    database must not cost one, which is why existence is checked before the
    fetch rather than relying on ON CONFLICT afterwards."""
    with _conn() as conn:
        _run(conn, youtube_creator)

        second_source = FakeTranscriptSource(TRANSCRIPTS)
        second = _run(conn, youtube_creator, transcript_source=second_source)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM creator_content WHERE creator_id = %s", (youtube_creator,)
            )
            content = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM creator_stances WHERE creator_id = %s", (youtube_creator,)
            )
            stances = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM content_chunks ch "
                "JOIN creator_content cc ON cc.id = ch.content_id WHERE cc.creator_id = %s",
                (youtube_creator,),
            )
            chunks_after = cur.fetchone()["n"]

    assert second.videos_ingested == 0
    assert second.transcript_stances_extracted == 0
    assert second.chunks_embedded == 0
    assert second_source.calls == []  # no fetch spent at all
    assert content == 2
    assert stances == 6
    assert chunks_after > 0


def test_a_video_with_no_transcript_is_counted_not_stored(youtube_creator):
    with _conn() as conn:
        summary = _run(conn, youtube_creator, transcript_source=FakeTranscriptSource({}))

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM creator_content WHERE creator_id = %s", (youtube_creator,)
            )
            count = cur.fetchone()["n"]

    assert summary.videos_without_transcript == 2
    assert summary.videos_ingested == 0
    assert count == 0


def test_an_ip_block_stops_the_transcript_leg_and_keeps_what_was_already_ingested(youtube_creator):
    blocked = FakeTranscriptSource(TRANSCRIPTS, blocked_after=1)

    with _conn() as conn:
        summary = _run(conn, youtube_creator, transcript_source=blocked)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM creator_content WHERE creator_id = %s", (youtube_creator,)
            )
            count = cur.fetchone()["n"]

    assert summary.transcripts_blocked is True
    assert summary.videos_ingested == 1
    assert count == 1  # the first video survives the block
    # It stopped rather than carrying on and digging the ban deeper.
    assert len(blocked.calls) == 2


def test_the_per_run_video_cap_is_respected(youtube_creator):
    many = [
        {
            "video_id": f"vid_{i}",
            "title": f"Video {i}",
            "published_at": days_ago(5 - i),
            "url": f"https://www.youtube.com/watch?v=vid_{i}",
        }
        for i in range(5)
    ]
    source = FakeTranscriptSource({v["video_id"]: TRANSCRIPTS["vid_new"] for v in many})

    with _conn() as conn:
        summary = _run(
            conn,
            youtube_creator,
            youtube_client=_FakeYouTubeClient(many),
            transcript_source=source,
            max_videos_per_creator=2,
        )

    assert summary.videos_ingested == 2
    assert len(source.calls) == 2


def test_a_creator_with_no_x_handle_is_never_sent_to_grok(youtube_creator):
    """_UnusedGrokClient raises if called, so simply completing proves it."""
    with _conn() as conn:
        summary = _run(conn, youtube_creator)

    assert summary.creators_processed == 1
    assert summary.videos_ingested == 2


def test_a_creator_with_no_youtube_source_checks_no_channel():
    """A creator with no channel is a no-op, not an error -- four of the
    ticket-06 roster are analytics accounts with no channel at all."""
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE name = %s", ("No Source Creator",))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, NULL, %s) "
                "RETURNING id",
                ("No Source Creator", "low"),
            )
            creator_id = cur.fetchone()["id"]
        conn.commit()

        summary = _run(conn, creator_id)

        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))
        conn.commit()

    assert summary.youtube_channels_checked == 0
    assert summary.videos_ingested == 0


# --- chunking, which is pure and needs no database -------------------------


def _timestamped(count: int, words_per_snippet: int = 40) -> str:
    body = " ".join(["word"] * words_per_snippet)
    return " ".join(f"[{i:02d}:00] {body}" for i in range(count))


def test_untimestamped_content_chunks_with_no_start_seconds():
    """An X post has no timeline, so start_seconds must be NULL rather than 0
    -- 0 would read as "the very start of a video" downstream."""
    chunks = _chunk_content("Buy Salah, great fixtures.")

    assert chunks == [("Buy Salah, great fixtures.", None)]


def test_empty_content_yields_no_chunks():
    assert _chunk_content("   ") == []


def test_a_long_transcript_is_split_into_several_chunks_each_with_its_timestamp():
    chunks = _chunk_content(_timestamped(30), max_chars=500, overlap_chars=100)

    assert len(chunks) > 1
    assert chunks[0][1] == 0
    # Every chunk knows where it starts, and they advance through the video.
    starts = [start for _, start in chunks]
    assert all(s is not None for s in starts)
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_chunks_overlap_so_a_claim_on_a_boundary_survives_intact():
    text = " ".join(f"[{i:02d}:00] segment{i} " + " ".join(["filler"] * 20) for i in range(20))
    chunks = _chunk_content(text, max_chars=400, overlap_chars=150)

    assert len(chunks) > 2
    # At least one segment appears in both the first chunk and the second, so a
    # claim sitting on the boundary lands whole in one of them.
    first = {w for w in chunks[0][0].split() if w.startswith("segment")}
    second = {w for w in chunks[1][0].split() if w.startswith("segment")}
    assert first & second
    # ...but they are not the same chunk twice: the second gets new material.
    assert second - first


def test_a_single_oversized_segment_still_becomes_one_chunk_rather_than_looping():
    """Guards the overlap step-back: backing past the chunk's own start index
    would never advance, and the loop would run forever."""
    huge = "[00:00] " + " ".join(["word"] * 500) + " [05:00] tail"
    chunks = _chunk_content(huge, max_chars=50, overlap_chars=40)

    assert len(chunks) == 2
    assert [start for _, start in chunks] == [0, 300]


def test_minutes_beyond_two_digits_are_parsed():
    """_timestamped_text renders total minutes, so a 100-minute deadline
    stream legitimately emits [100:30]."""
    chunks = _chunk_content("[100:30] late in a long stream")

    assert chunks == [("late in a long stream", 6030)]


def test_an_hours_minutes_seconds_marker_is_parsed():
    chunks = _chunk_content("[01:02:03] deep into a stream")

    assert chunks == [("deep into a stream", 3723)]


def test_an_unreachable_channel_feed_is_counted_rather_than_aborting_the_run(youtube_creator):
    """One channel's feed being down must not cost the other eighteen creators
    their run -- but it must be visible, or a degraded run reads as a quiet
    week."""

    class _FailingYouTubeClient:
        def list_recent_videos(self, channel_id: str) -> list[dict]:
            raise httpx.ConnectError("feed unreachable")

    with _conn() as conn:
        summary = _run(conn, youtube_creator, youtube_client=_FailingYouTubeClient())

    assert summary.youtube_channels_checked == 1
    assert summary.youtube_channels_failed == 1
    assert summary.videos_ingested == 0


def test_a_video_older_than_the_cutoff_costs_no_transcript_fetch(youtube_creator):
    """The relevance cutoff is also what bounds the walk-back. Without it a
    channel eventually costs 15 fetches, including pre-season and (for a
    dormant channel) years-old videos."""
    stale = [
        {
            "video_id": "vid_ancient",
            "title": "FPL 2010 season preview",
            "published_at": "2010-10-05T12:00:00+00:00",
            "url": "https://www.youtube.com/watch?v=vid_ancient",
        },
        VIDEOS[0],
    ]
    source = FakeTranscriptSource({"vid_new": TRANSCRIPTS["vid_new"], "vid_ancient": [("old", 0.0, 1.0)]})

    with _conn() as conn:
        summary = _run(
            conn, youtube_creator, youtube_client=_FakeYouTubeClient(stale), transcript_source=source
        )

    assert summary.videos_too_old == 1
    assert summary.videos_ingested == 1
    assert source.calls == ["vid_new"]  # the 2010 video never cost a fetch


def test_a_video_with_an_unparseable_date_is_still_fetched(youtube_creator):
    """Dropping it would silently lose real content over a feed-format change.
    The cap and the already-seen check still bound the cost."""
    odd = [{**VIDEOS[0], "published_at": "not-a-date"}]
    source = FakeTranscriptSource({"vid_new": TRANSCRIPTS["vid_new"]})

    with _conn() as conn:
        summary = _run(
            conn, youtube_creator, youtube_client=_FakeYouTubeClient(odd), transcript_source=source
        )

    assert summary.videos_too_old == 0
    assert summary.videos_ingested == 1


def test_the_age_cutoff_can_be_disabled(youtube_creator):
    """A backfill run wants everything the feed still carries."""
    old = [{**VIDEOS[0], "published_at": "2020-01-01T12:00:00+00:00"}]
    source = FakeTranscriptSource({"vid_new": TRANSCRIPTS["vid_new"]})

    with _conn() as conn:
        summary = _run(
            conn,
            youtube_creator,
            youtube_client=_FakeYouTubeClient(old),
            transcript_source=source,
            max_age_days=0,
        )

    assert summary.videos_too_old == 0
    assert summary.videos_ingested == 1
