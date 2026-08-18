"""Issue 10 / ADR-0012: log enough identity on each Recommendation and
Creator Stance to later match it against what actually happened, and fill in
that outcome once a subsequent ingestion cycle has the data. Nothing in v1
reads outcome_* -- this only exists so v2's accuracy scoring (ADR-0001) has
history instead of starting cold.
"""

from psycopg import Connection

from fpl_coach.instincts import current_gameweek


def log_recommendation(conn: Connection, team_id: int, player_id: int, player_name: str) -> None:
    """gameweek is whatever current_gameweek resolves to -- NULL if no team
    ingestion has ever run yet, same as creator_stances.gameweek. Unlike
    log_instinct, this never raises: a Recommendation is a byproduct of the
    user's actual question, not a request to log something, so it must not
    block the chat response just because the gameweek isn't known yet."""
    gameweek = current_gameweek(conn, team_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
            "VALUES (%s, %s, %s, %s)",
            (team_id, gameweek, player_id, player_name),
        )
    conn.commit()


def record_pending_outcomes(conn: Connection, elements_by_id: dict[int, dict], gameweek: int) -> int:
    """Called from run_ingestion once it has resolved the current gameweek
    and bootstrap-static's elements for it. Updates every recommendation_log
    / creator_stances row from exactly the immediately-prior gameweek
    (gameweek - 1) that hasn't had its outcome recorded yet.

    Deliberately NOT "any earlier gameweek" (gameweek < current): bootstrap-
    static's cost_change_event/event_points are only ever "this event"'s
    numbers, so a row from two or more gameweeks back would get the *latest*
    gameweek's price movement/points written onto it as if they were its
    own -- wrong data, not just missing data. If an ingestion cycle gets
    skipped (ADR-0002: on-demand, not scheduled) a row's window passes and
    its outcome stays NULL forever, which is the honest outcome given v1
    doesn't keep gameweek-by-gameweek history to catch up from.

    A row whose player_id/fpl_player_id isn't in this bootstrap-static
    snapshot (unmatched at Stance-extraction time, or the player has since
    left the game) is left with a NULL outcome rather than guessed at."""
    prior_gameweek = gameweek - 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, player_id FROM recommendation_log "
            "WHERE gameweek = %s AND outcome_recorded_at IS NULL AND player_id IS NOT NULL",
            (prior_gameweek,),
        )
        pending_recommendations = cur.fetchall()
        cur.execute(
            "SELECT id, fpl_player_id FROM creator_stances "
            "WHERE gameweek = %s AND outcome_recorded_at IS NULL AND fpl_player_id IS NOT NULL",
            (prior_gameweek,),
        )
        pending_stances = cur.fetchall()

    updated = 0
    updated += _apply_recommendation_outcomes(conn, pending_recommendations, elements_by_id)
    updated += _apply_stance_outcomes(conn, pending_stances, elements_by_id)

    if updated:
        conn.commit()
    return updated


def _apply_recommendation_outcomes(
    conn: Connection, pending_rows: list[dict], elements_by_id: dict[int, dict]
) -> int:
    updated = 0
    for row in pending_rows:
        element = elements_by_id.get(row["player_id"])
        if element is None:
            continue
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE recommendation_log SET outcome_price_change = %s, outcome_points = %s, "
                "outcome_recorded_at = now() WHERE id = %s",
                (element["cost_change_event"] / 10, element["event_points"], row["id"]),
            )
        updated += 1
    return updated


def _apply_stance_outcomes(
    conn: Connection, pending_rows: list[dict], elements_by_id: dict[int, dict]
) -> int:
    updated = 0
    for row in pending_rows:
        element = elements_by_id.get(row["fpl_player_id"])
        if element is None:
            continue
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE creator_stances SET outcome_price_change = %s, outcome_points = %s, "
                "outcome_recorded_at = now() WHERE id = %s",
                (element["cost_change_event"] / 10, element["event_points"], row["id"]),
            )
        updated += 1
    return updated
