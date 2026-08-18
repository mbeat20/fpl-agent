import os
import sys

from dotenv import load_dotenv

from fpl_coach.api.routes import MY_TEAM_ID
from fpl_coach.creators import VALID_TRUST_TIERS, add_creator
from fpl_coach.db import get_connection
from fpl_coach.ingestion.creator_pipeline import run_creator_ingestion
from fpl_coach.ingestion.extraction import ClaudeStanceExtractor
from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.grok_client import GrokClient
from fpl_coach.ingestion.pipeline import run_ingestion
from fpl_coach.ingestion.understat_client import UnderstatClient


def main() -> None:
    load_dotenv()
    fpl_entry_id = int(os.environ["FPL_ENTRY_ID"])
    understat_season = os.environ.get("UNDERSTAT_SEASON") or None

    client = FPLClient()
    understat_client = UnderstatClient()
    with get_connection() as conn:
        summary = run_ingestion(
            conn, client, understat_client, MY_TEAM_ID, fpl_entry_id, understat_season
        )

    print(
        f"Ingested {summary.team_name}: "
        f"{summary.players_ingested} players, {summary.fixtures_ingested} fixtures, "
        f"{summary.players_pool_ingested} pool players "
        f"({summary.understat_matched} matched to Understat {summary.understat_season} xG/xA)."
    )


def add_creator_main() -> None:
    load_dotenv()
    if len(sys.argv) != 4:
        print(f"Usage: fpl-add-creator <name> <x_handle> <trust_tier: {'|'.join(sorted(VALID_TRUST_TIERS))}>")
        raise SystemExit(1)
    name, x_handle, trust_tier = sys.argv[1], sys.argv[2], sys.argv[3]

    with get_connection() as conn:
        creator = add_creator(conn, name, x_handle.lstrip("@"), trust_tier)

    print(f"Added creator {creator.name} (@{creator.x_handle}, {creator.trust_tier} trust) as id {creator.id}.")


def ingest_creators_main() -> None:
    load_dotenv()
    grok_client = GrokClient()
    extractor = ClaudeStanceExtractor()
    with get_connection() as conn:
        summary = run_creator_ingestion(conn, grok_client, extractor)

    print(
        f"Processed {summary.creators_processed} creators: "
        f"{summary.content_ingested} new posts ingested, "
        f"{summary.content_skipped} already-seen posts skipped, "
        f"{summary.stances_extracted} stances extracted."
    )


if __name__ == "__main__":
    main()
