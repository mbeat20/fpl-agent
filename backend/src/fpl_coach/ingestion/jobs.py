"""One definition of "an ingestion run", shared by the CLI and the HTTP API.

The pipelines themselves (`pipeline.run_ingestion`, `creator_pipeline.
run_creator_ingestion`) take their external clients as arguments, which is
what makes them testable and is not going to change. But something has to
decide which real clients those are, and until now that something was
`cli.py` -- so the only way to trigger a refresh was a terminal, and adding a
button would have meant a second, drifting copy of the same wiring.

This module is that decision, made once. `cli.py` is now a thin printer over
it and the API routes schedule it as a background task, so a refresh started
from the browser and a refresh started with `fpl-ingest` are the same run,
recorded the same way in the same ledger. That equivalence is the point: a
freshness stamp that only knew about half the ways a refresh can happen would
be worse than none, because it would be confidently wrong.

`execute_run` opens its own connection rather than taking one. A background
task outlives the request that scheduled it, and the request's connection
closes when the response is sent.
"""

import os
import traceback

from dotenv import load_dotenv

from fpl_coach.db import get_connection
from fpl_coach.ingestion.creator_pipeline import CreatorIngestionSummary, run_creator_ingestion
from fpl_coach.ingestion.extraction import ClaudeStanceExtractor
from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.grok_client import GrokClient
from fpl_coach.ingestion.pipeline import IngestionSummary, run_ingestion
from fpl_coach.ingestion.runs import fail_run, finish_run
from fpl_coach.ingestion.transcripts import YouTubeTranscriptSource
from fpl_coach.ingestion.voyage_client import VoyageClient
from fpl_coach.ingestion.youtube_client import YouTubeClient
from fpl_coach.models import IngestionKind

# What each refresh actually costs, stated where the refresh is defined rather
# than in the component that draws the button.
#
# The FPL leg is ~80-100 requests to two public APIs and no model calls at
# all, so it is free and can be pressed as often as you like. The creator leg
# runs Grok's X Search per creator, Claude extraction per new post or
# transcript, and Voyage embedding per new chunk -- it costs real money per
# run and is rate-limited at the YouTube end. A UI that renders both
# identically would eventually cost the user money by accident.
BILLED: dict[IngestionKind, bool] = {"fpl": False, "creators": True}


def run_fpl_ingestion(team_id: int) -> IngestionSummary:
    """The FPL leg, with its real client.

    Squad, bank, XI, captain, chips, fixtures, the player pool, xG/xA (FPL's
    own, ADR-0022), the gameweek calendar, per-gameweek history and outcome
    resolution. Free and model-free.
    """
    load_dotenv()
    fpl_entry_id = int(os.environ["FPL_ENTRY_ID"])

    with get_connection() as conn:
        return run_ingestion(conn, FPLClient(), team_id, fpl_entry_id)


def run_creators_ingestion(team_id: int) -> CreatorIngestionSummary:
    """The creator leg, with its real clients. Billed -- see BILLED above."""
    load_dotenv()
    with get_connection() as conn:
        return run_creator_ingestion(
            conn,
            GrokClient(),
            ClaudeStanceExtractor(),
            VoyageClient(),
            team_id,
            youtube_client=YouTubeClient(),
            transcript_source=YouTubeTranscriptSource(),
        )


_RUNNERS = {"fpl": run_fpl_ingestion, "creators": run_creators_ingestion}


def execute_run(
    run_id: int, kind: IngestionKind, team_id: int, reraise: bool = False
) -> IngestionSummary | CreatorIngestionSummary | None:
    """Run the pipeline and close out its ledger row, whatever happens.

    `start_run` has already claimed the lock; this releases it. Every
    exception is caught and recorded, because an escaping one would leave the
    row `running` forever and every subsequent refresh would 409 until the
    two-hour reaper caught up.

    The recorded message is the exception's type and text plus the last
    frame, not the whole traceback: it is rendered in a panel, and the useful
    part of a psycopg or httpx failure is its first line.

    `reraise` is for the CLI, where there IS somebody to show a traceback to
    and a swallowed stack is actively unhelpful. The row is written either
    way first, so the ledger is correct whether the caller wants the
    exception or the None.
    """
    try:
        summary = _RUNNERS[kind](team_id)
    except Exception as exc:  # noqa: BLE001 -- see docstring
        detail = f"{type(exc).__name__}: {exc}"
        frames = traceback.extract_tb(exc.__traceback__)
        if frames:
            last = frames[-1]
            detail += f"  (at {os.path.basename(last.filename)}:{last.lineno} in {last.name})"
        with get_connection() as conn:
            fail_run(conn, run_id, detail)
        if reraise:
            raise
        return None

    with get_connection() as conn:
        finish_run(conn, run_id, summary)
    return summary
