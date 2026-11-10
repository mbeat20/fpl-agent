import re
import sys

from dotenv import load_dotenv

from fpl_coach.api.routes import MY_TEAM_ID
from fpl_coach.creators import (
    VALID_TRUST_TIERS,
    add_creator,
    add_creator_source,
    list_creator_sources,
)
from fpl_coach.db import get_connection
from fpl_coach.ingestion.jobs import execute_run
from fpl_coach.ingestion.runs import ABANDON_AFTER, RunAlreadyRunning, start_run
from fpl_coach.ingestion.youtube_client import YouTubeClient

# Issue 16: "no X handle" has to be sayable on a positional-argument CLI, for a
# creator followed on YouTube alone.
_NO_HANDLE = {"-", "none", "null"}


def _claim(kind: str) -> int:
    """Take the run lock, or explain who holds it and stop.

    A CLI run goes through the same ledger as a run started from the browser
    (jobs.py's docstring says why), which means the terminal can now be told
    "the dashboard already has one in flight" instead of quietly starting a
    second concurrent pass over a billed pipeline.
    """
    with get_connection() as conn:
        try:
            run = start_run(conn, MY_TEAM_ID, kind, trigger="cli")
        except RunAlreadyRunning as exc:
            print(exc)
            print(
                "  If you are sure nothing is running, it will be released "
                f"automatically {ABANDON_AFTER} after it started."
            )
            raise SystemExit(1) from None
    return run.id


def main() -> None:
    load_dotenv()
    run_id = _claim("fpl")
    summary = execute_run(run_id, "fpl", MY_TEAM_ID, reraise=True)
    assert summary is not None  # reraise=True means a failure never returns

    print(
        f"Ingested {summary.team_name}: "
        f"{summary.players_ingested} players, {summary.fixtures_ingested} fixtures, "
        f"{summary.players_pool_ingested} pool players "
        f"({summary.players_with_xg} with xG/xA), "
        f"{summary.outcomes_recorded} outcomes recorded, "
        f"{summary.chips_used} chips used, "
        f"minutes fetched for {summary.minutes_players_fetched} players, "
        f"{summary.gameweeks_of_history} gameweeks of history, "
        f"{summary.gameweeks_of_calendar} gameweek deadlines, "
        f"{summary.clubs_ingested} clubs."
    )


def add_creator_main() -> None:
    load_dotenv()
    if len(sys.argv) != 4:
        print(
            f"Usage: fpl-add-creator <name> <x_handle|-> "
            f"<trust_tier: {'|'.join(sorted(VALID_TRUST_TIERS))}>"
        )
        print("  Pass - as the x_handle for a creator followed only on YouTube.")
        raise SystemExit(1)
    name, x_handle, trust_tier = sys.argv[1], sys.argv[2], sys.argv[3]
    handle = None if x_handle.strip().lower() in _NO_HANDLE else x_handle.lstrip("@")

    with get_connection() as conn:
        creator = add_creator(conn, name, handle, trust_tier)

    where = f"@{creator.x_handle} on X" if creator.x_handle else "no X handle"
    print(f"Added creator {creator.name} ({where}, {creator.trust_tier} trust) as id {creator.id}.")


def _youtube_handle_candidates(name: str, x_handle: str | None) -> list[str]:
    """YouTube handles are not X handles -- @LetsTalk_FPL on X is @LetsTalkFPL
    on YouTube -- so guessing is worth a try before asking the user, but only
    a few obvious shapes. Anything less obvious is passed explicitly."""
    raw = [x_handle, (x_handle or "").replace("_", ""), re.sub(r"[^A-Za-z0-9]", "", name)]
    seen: set[str] = set()
    candidates = []
    for candidate in raw:
        if candidate and candidate.lower() not in seen:
            seen.add(candidate.lower())
            candidates.append(candidate)
    return candidates


def link_youtube_main() -> None:
    """fpl-link-youtube [<creator name or x_handle> <youtube_handle>]

    With no arguments, tries to resolve a YouTube channel for every creator
    that has not got one yet, and reports which resolved and which did not --
    an unresolvable creator is a recorded no-op, not an error, because four of
    the ticket-06 roster are data/analytics accounts with no channel at all.
    """
    load_dotenv()
    args = sys.argv[1:]
    if len(args) not in (0, 2):
        print("Usage: fpl-link-youtube [<creator name or x_handle> <youtube_handle>]")
        raise SystemExit(1)

    youtube_client = YouTubeClient()
    with get_connection() as conn:
        if len(args) == 2:
            _link_one(conn, youtube_client, args[0], args[1].lstrip("@"))
        else:
            _link_all(conn, youtube_client)


def _link_one(conn, youtube_client: YouTubeClient, creator_ref: str, youtube_handle: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name FROM creators WHERE name = %s OR x_handle = %s",
            (creator_ref, creator_ref.lstrip("@")),
        )
        creator = cur.fetchone()
    if creator is None:
        print(f"No creator matching {creator_ref!r}. Add them with fpl-add-creator first.")
        raise SystemExit(1)

    channel_id = youtube_client.resolve_channel_id(youtube_handle)
    if channel_id is None:
        print(f"@{youtube_handle} does not resolve to a YouTube channel -- nothing linked.")
        raise SystemExit(1)

    source = add_creator_source(conn, creator["id"], "youtube", channel_id, youtube_handle)
    print(f"Linked {creator['name']} -> @{youtube_handle} ({source.external_id}).")


def _link_all(conn, youtube_client: YouTubeClient) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, x_handle FROM creators ORDER BY id")
        creators = cur.fetchall()

    already = {s.creator_id for s in list_creator_sources(conn, platform="youtube")}
    linked, skipped, unresolved = [], [], []

    for creator in creators:
        if creator["id"] in already:
            skipped.append(creator["name"])
            continue
        channel_id = None
        handle = None
        for candidate in _youtube_handle_candidates(creator["name"], creator["x_handle"]):
            channel_id = youtube_client.resolve_channel_id(candidate)
            if channel_id is not None:
                handle = candidate
                break
        if channel_id is None:
            unresolved.append(creator["name"])
            continue
        add_creator_source(conn, creator["id"], "youtube", channel_id, handle)
        linked.append(f"{creator['name']} -> @{handle} ({channel_id})")

    print(f"Linked {len(linked)}:")
    for line in linked:
        print(f"  {line}")
    print(f"Already linked {len(skipped)}: {', '.join(skipped) or '-'}")
    print(f"No channel found for {len(unresolved)}: {', '.join(unresolved) or '-'}")
    if unresolved:
        print(
            "  A creator with no channel is a no-op every run, not an error. Link one "
            "explicitly with: fpl-link-youtube <creator> <youtube_handle>"
        )


def ingest_creators_main() -> None:
    load_dotenv()
    run_id = _claim("creators")
    summary = execute_run(run_id, "creators", MY_TEAM_ID, reraise=True)
    assert summary is not None  # reraise=True means a failure never returns

    print(
        f"Processed {summary.creators_processed} creators: "
        f"{summary.content_ingested} new posts/videos ingested, "
        f"{summary.content_skipped} already-seen skipped, "
        f"{summary.stances_extracted} stances extracted, "
        f"{summary.chunks_embedded} chunks embedded."
    )
    print(
        f"  YouTube: {summary.youtube_channels_checked} channels checked, "
        f"{summary.videos_ingested} transcripts ingested "
        f"({summary.transcript_stances_extracted} stances), "
        f"{summary.videos_without_transcript} videos had no transcript, "
        f"{summary.videos_too_old} too old to bother with."
    )
    if summary.youtube_channels_failed:
        print(
            f"  {summary.youtube_channels_failed} channel feeds were unreachable this run "
            "and contributed nothing -- a quiet result here is not the same as a quiet week."
        )
    if summary.transcripts_blocked:
        print(
            "  YouTube blocked this IP mid-run, so the transcript leg stopped early. "
            "Everything ingested before that stands and the next run resumes from there. "
            "If this keeps happening, set YOUTUBE_PROXY_URL."
        )


if __name__ == "__main__":
    main()
