import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from psycopg import Connection

from fpl_coach.gameweek import gameweek_at, load_deadlines, planning_gameweek
from fpl_coach.ingestion.extraction import STANCE_VOCABULARY, StanceExtractor
from fpl_coach.ingestion.grok_client import GrokClient
from fpl_coach.ingestion.transcripts import Transcript, TranscriptBlocked, TranscriptSource
from fpl_coach.ingestion.voyage_client import VoyageClient, vector_literal
from fpl_coach.ingestion.youtube_client import YouTubeClient
from fpl_coach.player_names import match_player_name
from fpl_coach.subjects import club_names, normalise_subject
from fpl_coach.tools.get_player import player_pool

# ~500 tokens at the usual ~4 chars/token, with an overlap so a claim
# straddling a boundary survives in at least one chunk intact.
_CHUNK_CHARS = 2000
_CHUNK_OVERLAP_CHARS = 300

# Issue 16: the transcript-fetch budget per creator per run. YouTube's Atom
# feed only ever returns the latest 15 videos, but fetching all of them for
# every creator is exactly the request burst that got this machine's IP
# blocked (see YouTubeTranscriptSource). Newest-first within this cap, so a
# missed run loses the oldest videos rather than the freshest ones.
_MAX_VIDEOS_PER_CREATOR = 3

# Issue 16: how far back a video is still worth a transcript fetch. Two weeks
# covers the current and previous gameweek, which is as far back as FPL
# advice stays useful -- a GW1 captaincy take is worthless in GW5.
_MAX_VIDEO_AGE_DAYS = 14

# Minutes are 1-3 digits, not 2: _timestamped_text renders total minutes, so a
# 100-minute deadline stream legitimately emits [100:30]. The optional third
# group accepts an H:MM:SS rendering from any other source.
_TIMESTAMP_MARKER = re.compile(r"\[(\d{1,3}):(\d{2})(?::(\d{2}))?\]")

# Issue 18: X post ids are Snowflakes -- the publish time is encoded in the id
# that every source_url already carries. X's epoch, not Unix's.
_X_STATUS_ID = re.compile(r"/status/(\d+)")
_X_SNOWFLAKE_EPOCH_MS = 1288834974657
# Ids minted before Snowflake (to late 2010) were sequential and topped out
# around 3e10; every Snowflake id since is above 2.9e16. Anything below this
# threshold is a legacy id whose bits are not a timestamp -- shifting it
# yields a date just after the epoch rather than an obviously wrong one,
# which is exactly the kind of plausible-looking nonsense worth rejecting.
_X_MIN_SNOWFLAKE_ID = 10**15


@dataclass(frozen=True)
class CreatorIngestionSummary:
    creators_processed: int
    content_ingested: int
    content_skipped: int
    stances_extracted: int
    chunks_embedded: int
    # Issue 16: the YouTube leg reported separately from the X leg, so a run
    # where transcripts silently contributed nothing is visible rather than
    # hidden inside a healthy-looking total.
    youtube_channels_checked: int = 0
    youtube_channels_failed: int = 0
    videos_ingested: int = 0
    videos_without_transcript: int = 0
    videos_too_old: int = 0
    transcript_stances_extracted: int = 0
    transcripts_blocked: bool = False


def run_creator_ingestion(
    conn: Connection,
    grok_client: GrokClient,
    extractor: StanceExtractor,
    voyage_client: VoyageClient,
    team_id: int,
    creator_ids: list[int] | None = None,
    youtube_client: YouTubeClient | None = None,
    transcript_source: TranscriptSource | None = None,
    max_videos_per_creator: int = _MAX_VIDEOS_PER_CREATOR,
    max_age_days: int = _MAX_VIDEO_AGE_DAYS,
) -> CreatorIngestionSummary:
    """Per creator: pulls recent posts via Grok's X Search (issue 06) and, for
    any creator with a YouTube source attached, full timestamped transcripts
    of that channel's recent videos (issue 16). Each is stored as
    creator_content (idempotent -- ON CONFLICT DO NOTHING on the
    UNIQUE (creator_id, source_url), since content accumulates across runs
    rather than getting replaced), then run through Layer-2 extraction into
    creator_stances.

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

    team_id (issue 10): each extracted Stance carries a gameweek so a later
    ingestion cycle can record its outcome.

    Issue 19 changed where that gameweek comes from. It used to be one
    snapshot per RUN -- and a run started before a deadline and finished
    after it, or simply pulled six days of posts, tagged every one of them
    the same. That is how 591 Stances from the 2026-08-27 batch ended up
    filed under GW1 while being about GW2. It is now derived per item, from
    that item's own publish date against the deadline calendar: content
    published in the run-up to a deadline is about the gameweek that deadline
    belongs to, whatever the ingestion run thought the date was.

    team_id's *planning* gameweek survives only as the fallback for content
    with no publish date at all. Planning, not gameweek_state's persisted
    value: creator content is hoovered up in the days before a deadline,
    which is exactly the window in which FPL still reports the finished
    gameweek as current. Taken as a parameter rather than importing
    MY_TEAM_ID here directly, so this ingestion module doesn't reach into the
    api layer -- the CLI passes MY_TEAM_ID.

    youtube_client/transcript_source (issue 16) are optional: passing neither
    reduces this to exactly the ticket-06 X-only behaviour, which is what
    every pre-existing test relies on.
    """
    with conn.cursor() as cur:
        if creator_ids is None:
            cur.execute("SELECT id, x_handle FROM creators")
        else:
            cur.execute("SELECT id, x_handle FROM creators WHERE id = ANY(%s)", (creator_ids,))
        creators = cur.fetchall()

    gameweek = planning_gameweek(conn, team_id)
    # Issue 19: the run's planning gameweek stops being the tag and becomes
    # only the fallback for undated content. The tag now comes from each
    # item's own publish date against the deadline calendar -- see
    # _gameweek_for below.
    deadlines, _ = load_deadlines(conn)
    tag_gameweek = _make_tagger(deadlines, gameweek)
    totals = {"ingested": 0, "skipped": 0, "stances": 0}

    for creator in creators:
        # Issue 16: x_handle is nullable now -- a YouTube-only creator has no
        # X presence to search, and passing None to Grok would search for the
        # literal string "None".
        if creator["x_handle"]:
            _ingest_x_posts(conn, grok_client, extractor, creator, tag_gameweek, totals)
        conn.commit()

    youtube = _ingest_youtube(
        conn,
        youtube_client,
        transcript_source,
        extractor,
        [c["id"] for c in creators],
        tag_gameweek,
        max_videos_per_creator,
        max_age_days,
        totals,
    )

    chunks_embedded = _embed_pending_content(conn, voyage_client, [c["id"] for c in creators])

    return CreatorIngestionSummary(
        creators_processed=len(creators),
        content_ingested=totals["ingested"],
        content_skipped=totals["skipped"],
        stances_extracted=totals["stances"],
        chunks_embedded=chunks_embedded,
        **youtube,
    )


def _make_tagger(deadlines, fallback: int | None):
    """Issue 19: `published_at -> gameweek`, closed over the calendar so the
    deadline table is read once per run rather than once per post.

    The fallback is the run's planning gameweek and is used only for content
    we could not date -- which today means an X post whose URL carries no
    decodable Snowflake id. Tagging those NULL instead would be more precise
    and less useful: outcome recording (ADR-0012) matches on this column, so
    an untagged Stance can never have its outcome resolved. The run's
    gameweek is the best available guess for something ingested during it.

    An empty calendar collapses this back to exactly the pre-issue-19
    behaviour, so a database with no fixtures and no gameweeks ingested still
    tags rather than failing.
    """

    def tag(published_at):
        return gameweek_at(deadlines, published_at) or fallback

    return tag


def _ingest_x_posts(conn, grok_client, extractor, creator, tag_gameweek, totals) -> None:
    posts = grok_client.search_creator_posts(creator["x_handle"])
    for post in posts:
        published_at = _x_published_at(post["url"])
        content_id = _insert_content(
            conn,
            creator["id"],
            post["url"],
            post["text"],
            platform="x",
            published_at=published_at,
        )
        if content_id is None:
            totals["skipped"] += 1
            continue
        totals["ingested"] += 1
        totals["stances"] += _extract_and_store(
            conn, extractor, creator["id"], content_id, post["text"],
            tag_gameweek(published_at), timestamped=False,
        )


def _ingest_youtube(
    conn,
    youtube_client,
    transcript_source,
    extractor,
    creator_id_list,
    tag_gameweek,
    max_videos_per_creator,
    max_age_days,
    totals,
) -> dict:
    result = {
        "youtube_channels_checked": 0,
        "youtube_channels_failed": 0,
        "videos_ingested": 0,
        "videos_without_transcript": 0,
        "videos_too_old": 0,
        "transcript_stances_extracted": 0,
        "transcripts_blocked": False,
    }
    if youtube_client is None or transcript_source is None or not creator_id_list:
        return result

    with conn.cursor() as cur:
        cur.execute(
            "SELECT creator_id, external_id FROM creator_sources "
            "WHERE platform = 'youtube' AND creator_id = ANY(%s) ORDER BY creator_id",
            (creator_id_list,),
        )
        sources = cur.fetchall()

    for source in sources:
        if result["transcripts_blocked"]:
            break
        result["youtube_channels_checked"] += 1
        try:
            videos = youtube_client.list_recent_videos(source["external_id"])
        except httpx.HTTPError:
            # One channel's feed being unreachable must not cost the other
            # eighteen creators their run. Counted, so a silently-degraded run
            # is visible rather than looking like a quiet week.
            result["youtube_channels_failed"] += 1
            continue

        # The feed is newest-first and already-seen videos are skipped, so on a
        # re-run the cap spends its budget on the next-oldest unseen videos --
        # walking backwards through the 15-video window a few at a time rather
        # than demanding one big burst, which is what triggers the 429.
        #
        # max_age_days is what stops that walk-back being unbounded. Without it
        # a channel costs 15 fetches spread over five runs, including videos
        # from before the season and, for a dormant channel, from years ago.
        # FPL advice also decays fast enough that an old video is near-worthless
        # even when it is free -- so this is a relevance cutoff first and a
        # budget cap second.
        fetched = 0
        for video in videos:
            if fetched >= max_videos_per_creator:
                break
            published = _parse_published_at(video["published_at"])
            if _is_stale(published, max_age_days):
                result["videos_too_old"] += 1
                continue
            # Check before fetching, not after: a transcript fetch is the
            # rate-limited resource, so spending one on a video already in
            # the database is the expensive mistake. ON CONFLICT DO NOTHING
            # below still guards the race.
            if _content_exists(conn, source["creator_id"], video["url"]):
                totals["skipped"] += 1
                continue

            try:
                transcript = transcript_source.fetch(video["video_id"])
            except TranscriptBlocked:
                # Every further request extends the ban, so stop the whole
                # transcript leg. Everything already committed stands, and the
                # next run picks up where this one left off.
                result["transcripts_blocked"] = True
                break

            fetched += 1
            if transcript is None or not transcript.snippets:
                result["videos_without_transcript"] += 1
                continue

            content_text = _timestamped_text(transcript)
            content_id = _insert_content(
                conn,
                source["creator_id"],
                video["url"],
                content_text,
                platform="youtube",
                published_at=published,
                title=video["title"],
                duration_seconds=transcript.duration_seconds,
            )
            if content_id is None:
                totals["skipped"] += 1
                continue

            result["videos_ingested"] += 1
            totals["ingested"] += 1
            # Once per transcript, whole -- not once per chunk. Chunks exist
            # for pgvector retrieval; extracting per chunk would cost more and
            # lose claims that span the video.
            stored = _extract_and_store(
                conn, extractor, source["creator_id"], content_id, content_text,
                tag_gameweek(published), timestamped=True,
            )
            result["transcript_stances_extracted"] += stored
            totals["stances"] += stored
            conn.commit()

        conn.commit()

    return result


def _parse_published_at(published_at: str | None) -> datetime | None:
    """The feed's `published` value as a datetime, or None if it cannot be
    parsed. Parsed once here rather than handed to psycopg as a string: a
    TIMESTAMPTZ column rejects a malformed date, so a feed-format change would
    abort a whole ingestion run instead of degrading to an unknown date."""
    if not published_at:
        return None
    try:
        parsed = datetime.fromisoformat(published_at)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _x_published_at(source_url: str) -> datetime | None:
    """When an X post was published, decoded from the post id in its own URL.

    Grok's X Search returns only {url, text} -- no date field -- so without
    this every X-sourced Stance is undated, and undated Stances cannot be
    ranked by recency or told apart from a five-month-old pre-season take
    (issue 18). The id in /status/<id> is a Snowflake: its top 41 bits are
    milliseconds since X's own epoch, so the publish time is already sitting
    in data we store, needing no API call and no re-fetch.

    None rather than a guess when the URL carries no id, when the id predates
    Snowflake, or when the decoded time is in the future.
    """
    match = _X_STATUS_ID.search(source_url)
    if match is None:
        return None
    post_id = int(match.group(1))
    if post_id < _X_MIN_SNOWFLAKE_ID:
        return None
    milliseconds = (post_id >> 22) + _X_SNOWFLAKE_EPOCH_MS
    try:
        published = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if published > datetime.now(timezone.utc):
        return None
    return published


def _is_stale(published: datetime | None, max_age_days: int) -> bool:
    """True when a video is too old to be worth a transcript fetch.

    An unknown date is treated as NOT stale. Skipping it would silently drop
    real content over a feed-format change, and the already-ingested check plus
    the per-creator cap still bound the cost.
    """
    if published is None or max_age_days <= 0:
        return False
    return published < datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _content_exists(conn: Connection, creator_id: int, source_url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM creator_content WHERE creator_id = %s AND source_url = %s",
            (creator_id, source_url),
        )
        return cur.fetchone() is not None


def _insert_content(
    conn: Connection,
    creator_id: int,
    source_url: str,
    content_text: str,
    platform: str,
    published_at: datetime | None = None,
    title: str | None = None,
    duration_seconds: int | None = None,
) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO creator_content "
            "(creator_id, source_url, content_text, platform, published_at, title, duration_seconds) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (creator_id, source_url) DO NOTHING "
            "RETURNING id",
            (creator_id, source_url, content_text, platform, published_at, title, duration_seconds),
        )
        row = cur.fetchone()
    return row["id"] if row is not None else None


def _extract_and_store(
    conn: Connection,
    extractor: StanceExtractor,
    creator_id: int,
    content_id: int,
    text: str,
    gameweek: int | None,
    timestamped: bool,
) -> int:
    """Extract one piece of content's stances and store them, never letting a
    failure on one item end the run.

    On 2026-08-30 a single malformed stance (`blank`, outside the vocabulary)
    raised a CheckViolation that propagated out of `run_creator_ingestion` and
    killed an ingestion mid-flight -- after ~92 pieces of content had been
    fetched and paid for. Everything downstream of the failure, including the
    embedding pass, simply did not happen.

    `refresh_squad_minutes` already learned this lesson in its own module:
    "One player's history failing must not abort a whole ingestion run."
    The same applies here, and more expensively, because each item costs a
    Grok search or a transcript fetch plus a Claude extraction before it can
    fail.

    A SAVEPOINT (`conn.transaction()` nested inside the open transaction), not
    a bare `conn.rollback()`. The difference is load-bearing and was found by
    the test below: the X path inserts the content row and extracts its
    stances in one uncommitted transaction, so rolling the whole thing back
    discards the *content* too -- losing the Grok search that was already paid
    for, and leaving the in-memory `ingested` counter claiming a row that no
    longer exists. A savepoint undoes only the stance work, so the content
    survives, gets embedded, and its stances can be re-extracted on a later
    run.
    """
    try:
        with conn.transaction():
            stances = extractor.extract(text, timestamped=timestamped)
            return _store_stances(conn, creator_id, content_id, stances, gameweek)
    except Exception:  # noqa: BLE001 -- one item must not end the run
        return 0


def in_range_start_seconds(value: object, duration_seconds: int | None) -> int | None:
    """The extractor's `start_seconds`, or None when it cannot be a real
    position in this piece of content.

    ADR-0014 says a citation is looked up and never authored by the model.
    `start_seconds` was the one part of a citation still taken on the model's
    word, and on the live corpus **162 of 1,446 video deep links (11%) pointed
    past the end of their own video** -- one at `&t=113520s` on a video 7,035
    seconds long. The error is systematic: the transcript is rendered with
    `[MM:SS]` markers and the model periodically reads one as `H:MM`, so
    `[31:32]` becomes 31 hours 32 minutes instead of 1,892 seconds.

    Out of range is stored as NULL rather than repaired. Reinterpreting
    113,520 back to 1,892 would land inside the video and look right, but it
    is a guess at which mistake the model made, and a citation that points
    confidently at the wrong sentence is worse than one that opens the video
    at the start -- which is exactly what ticket 17 already does for content
    with no timestamp at all. STATUS.md §7: degrade coarsely, never to
    something that resembles an answer.

    A missing duration means nothing can be checked, so the value passes.
    That is not a gap in practice -- every video row carries one -- but it
    keeps a text post, which has no duration, unaffected.
    """
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    if duration_seconds is not None and seconds > duration_seconds:
        return None
    return seconds


def _content_duration(conn: Connection, content_id: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT duration_seconds FROM creator_content WHERE id = %s", (content_id,))
        row = cur.fetchone()
    return row["duration_seconds"] if row is not None else None


def _store_stances(
    conn: Connection, creator_id: int, content_id: int, stances: list[dict], gameweek: int | None
) -> int:
    """A stance needs either a player or a subject (schema.sql's
    creator_stances_subject_check). One with neither is dropped rather than
    stored with a fabricated subject -- an unattributable opinion is worse
    than no opinion, since ticket 19 would count it in a consensus.

    The same applies to the stance value itself. `record_stances` declares
    `"enum": STANCE_VOCABULARY`, but a tool-schema enum is a request, not a
    guarantee -- on 2026-08-30 the extractor returned `blank` (a real FPL
    idea, "expected to score nothing", that the vocabulary spells `avoid`).
    schema.sql's CHECK constraint then rejected the row and the
    CheckViolation propagated all the way out of run_creator_ingestion,
    destroying an entire ingestion run over one malformed stance. So the
    vocabulary is enforced HERE, at the boundary where the model's output
    stops being trusted, rather than being discovered at the database.
    """
    # Loaded once for the whole item rather than once per Stance: a
    # team-selection transcript yields over a hundred, and the pool is ~650
    # rows that cannot change mid-item. `match_player_name` is used directly
    # instead of `find_player` because only the id is wanted here -- the
    # Player row would be fetched and thrown away.
    pool = player_pool(conn)
    clubs = club_names(conn)
    duration = _content_duration(conn, content_id)
    stored = 0
    for stance in stances:
        player_name = stance.get("player_name")
        # Issue 29: canonicalised here rather than left as the creator's
        # phrasing. Ticket 19 counts distinct creators per view, so `wildcard`
        # / `wild card` / `Wildcard` arriving as three subjects split one
        # chip's consensus three ways.
        subject = normalise_subject(stance.get("subject"), clubs)
        if not player_name and not subject:
            continue
        if stance.get("stance") not in STANCE_VOCABULARY:
            # Dropped, not coerced to a neighbouring value: guessing that
            # `blank` meant `avoid` would put words the creator did not say
            # into a trust record, and ticket 19 would then count them.
            continue
        matched = match_player_name(player_name, pool) if player_name else None
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, subject, fpl_player_id, "
                " stance, confidence, reason, gameweek, start_seconds) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    creator_id,
                    content_id,
                    player_name,
                    subject,
                    matched,
                    stance["stance"],
                    stance["confidence"],
                    stance["reason"],
                    gameweek,
                    in_range_start_seconds(stance.get("start_seconds"), duration),
                ),
            )
        stored += 1
    return stored


def _timestamped_text(transcript: Transcript) -> str:
    """Renders a transcript as its content_text with inline [MM:SS] markers.

    The markers are stored in the text on purpose rather than stripped: they
    are what lets the extractor place a claim at a moment, and what lets
    _chunk_content recover each chunk's start_seconds later during the
    embedding backfill -- which reads content_text alone and has no access to
    the original snippets. Storing bare prose would make the timestamps
    unrecoverable for any row already in the database.
    """
    parts = []
    for snippet in transcript.snippets:
        text = snippet.text.strip()
        if not text:
            continue
        total = int(snippet.start)
        parts.append(f"[{total // 60:02d}:{total % 60:02d}] {text}")
    return " ".join(parts)


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
        chunks = _chunk_content(row["content_text"])
        if not chunks:
            continue
        embeddings = voyage_client.embed([text for text, _ in chunks])
        with conn.cursor() as cur:
            for (chunk_text, start_seconds), embedding in zip(chunks, embeddings):
                cur.execute(
                    "INSERT INTO content_chunks (content_id, chunk_text, embedding, start_seconds) "
                    "VALUES (%s, %s, %s::vector, %s)",
                    (row["id"], chunk_text, vector_literal(embedding), start_seconds),
                )
        chunks_embedded += len(chunks)

    if pending:
        conn.commit()
    return chunks_embedded


def _chunk_content(
    text: str, max_chars: int = _CHUNK_CHARS, overlap_chars: int = _CHUNK_OVERLAP_CHARS
) -> list[tuple[str, int | None]]:
    """Splits content into overlapping chunks, each paired with the second
    into the source video it starts at (None for content with no timeline).

    Issue 16 replaced a flat 2000-char split that had never actually split
    anything -- 295 chunks for 290 rows -- because Grok's paraphrases average
    115 words. A 6,000-word transcript needs real chunking, and needs each
    chunk to keep its timestamp so a pgvector hit resolves to a moment rather
    than to a whole hour-long video.
    """
    text = text.strip()
    if not text:
        return []

    segments = _timestamped_segments(text)
    if segments is None:
        return [(text[i : i + max_chars], None) for i in range(0, len(text), max_chars)]

    chunks: list[tuple[str, int | None]] = []
    index = 0
    while index < len(segments):
        start_seconds = segments[index][0]
        parts: list[str] = []
        length = 0
        cursor = index
        while cursor < len(segments) and (
            not parts or length + len(segments[cursor][1]) <= max_chars
        ):
            parts.append(segments[cursor][1])
            length += len(segments[cursor][1]) + 1
            cursor += 1
        chunks.append((" ".join(parts), start_seconds))
        if cursor >= len(segments):
            break
        # Step back far enough to overlap the tail of the chunk just emitted,
        # so a claim spanning the boundary lands whole in one of the two. Never
        # back past index + 1, which would not advance and would loop forever.
        back = cursor
        overlap = 0
        while back > index + 1 and overlap < overlap_chars:
            back -= 1
            overlap += len(segments[back][1]) + 1
        index = back
    return chunks


def _timestamped_segments(text: str) -> list[tuple[int, str]] | None:
    """[(start_seconds, text)] for content rendered with [MM:SS] markers, or
    None when there are none -- an X post has no timeline."""
    matches = list(_TIMESTAMP_MARKER.finditer(text))
    if not matches:
        return None

    segments = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue
        hours_or_minutes, minutes_or_seconds, maybe_seconds = match.groups()
        if maybe_seconds is None:
            seconds = int(hours_or_minutes) * 60 + int(minutes_or_seconds)
        else:
            seconds = (
                int(hours_or_minutes) * 3600
                + int(minutes_or_seconds) * 60
                + int(maybe_seconds)
            )
        segments.append((seconds, body))
    return segments or None
