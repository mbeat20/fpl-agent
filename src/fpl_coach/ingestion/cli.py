import os

from dotenv import load_dotenv

from fpl_coach.api.routes import MY_TEAM_ID
from fpl_coach.db import get_connection
from fpl_coach.ingestion.fpl_client import FPLClient
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


if __name__ == "__main__":
    main()
