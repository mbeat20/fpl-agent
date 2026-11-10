"""Issue 15: cached unprompted weekly suggestion for the Team dashboard.

One row per team (team_suggestions.team_id is its own primary key, same
convention as gameweek_state) -- regenerating for a new gameweek overwrites
the previous row rather than accumulating a history GET /team/suggestion has
no use for (recommendation_log already keeps that trail via
log_recommendation).
"""

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from fpl_coach.models import Recommendation, TransferPlan


def get_cached_suggestion(
    conn: Connection, team_id: int, gameweek: int
) -> Recommendation | TransferPlan | None:
    """None unless a suggestion was already generated for this exact
    gameweek -- a stale prior-gameweek row is never returned, the same
    "invisible once the gameweek moves on" behaviour issue 13's
    get_instincts already relies on for its own gameweek scoping."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT recommendation FROM team_suggestions WHERE team_id = %s AND gameweek = %s",
            (team_id, gameweek),
        )
        row = cur.fetchone()
    if row is None:
        return None
    data = row["recommendation"]
    try:
        return TransferPlan(**data) if "steps" in data else Recommendation(**data)
    except ValidationError:
        # A cached row written before the current Recommendation/TransferPlan
        # shape (e.g. one predating the `decision` field) is unreadable now.
        # This is a cache, so the only sane failure mode is a miss -- the
        # caller regenerates and save_suggestion overwrites the row. Raising
        # here would instead take the dashboard down until someone manually
        # cleared the table.
        return None


def save_suggestion(
    conn: Connection, team_id: int, gameweek: int, result: Recommendation | TransferPlan
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO team_suggestions (team_id, gameweek, recommendation) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek, "
            "recommendation = EXCLUDED.recommendation, created_at = now()",
            (team_id, gameweek, Jsonb(result.model_dump(mode="json"))),
        )
    conn.commit()
