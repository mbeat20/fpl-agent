"""Issue 10 / ADR-0012: log enough identity on each Recommendation and
Creator Stance to later match it against what actually happened, and fill in
that outcome once the data exists.

Issue 24 changes *when* "once the data exists" can be. The original
implementation resolved a row by reading bootstrap-static's
`cost_change_event` and `event_points`, which are only ever **this event's**
numbers. That forced a one-gameweek window -- resolve only rows from exactly
`gameweek - 1`, because a row two gameweeks old would have the latest
gameweek's points written onto it as though they were its own. Wrong data is
worse than missing data, so the narrow window was right given that source.

The consequence was not: ingestion is manual (ADR-0002, no scheduler), so any
skipped week let a row's window pass and its outcome stayed NULL **forever**.
ADR-0001 defers automatic creator trust-scoring to v2 on the basis that v1 is
collecting this history, and it was quietly not collecting it.

The fix is to stop depending on a "current event" source, not to widen the
window. `player_gameweek_history` (ticket 20) already holds per-gameweek
`total_points` from `element-summary`, and ticket 24 adds each gameweek's
`value` alongside it. Both are per-gameweek facts that do not expire, so a row
from four gameweeks ago resolves *correctly* and late.

Three things this is careful about:

- **Driven by rows lacking an outcome, never by what this run touched.** The
  same lesson as ticket 06's embedding backfill
  ([[project_ingestion_backfill_pattern]]): keying off "what changed in this
  cycle" permanently skips everything that already existed.
- **An absent history row is a real zero, not missing data.** FPL emits a
  history entry per fixture even at nil minutes, so a player we hold history
  *through* a gameweek but with no row *at* it genuinely did not feature --
  that is 0 points. A player we hold nothing for is unknown, and stays NULL.
- **Unknown stays NULL.** A player who cannot be found, or a gameweek whose
  history has not been fetched, is left alone rather than guessed at.
"""

from dataclasses import dataclass

from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek
from fpl_coach.squad_state import latest_finished_gameweek


def log_recommendation(
    conn: Connection, team_id: int, player_id: int, player_name: str, gameweek: int | None = None
) -> None:
    """gameweek defaults to the *planning* gameweek -- the one the advice
    actually targets -- NULL only if neither ingestion nor fixtures know one
    yet. Not gameweek_state's persisted value: that names the finished
    gameweek right up until the next kicks off, so a captaincy call made on
    deadline eve would be filed against the week whose fixtures had already
    been played, and outcome resolution would then score it on that week's
    points. Tagging and outcome resolution have to agree on which gameweek the
    advice was for.
    Issue 12: a TransferPlan step already knows its own (possibly future)
    gameweek, which log_recommendation must use as-is rather than silently
    overwriting with the planning gameweek. Unlike log_instinct, this never
    raises: a Recommendation is a byproduct of the user's actual question, not
    a request to log something, so it must not block the chat response just
    because the gameweek isn't known yet."""
    if gameweek is None:
        gameweek = planning_gameweek(conn, team_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
            "VALUES (%s, %s, %s, %s)",
            (team_id, gameweek, player_id, player_name),
        )
    conn.commit()


@dataclass(frozen=True)
class OutcomeBackfill:
    """What a catch-up run actually managed, reported rather than summed into
    one number -- "3 resolved" and "3 resolved, 40 still unresolvable because
    nothing has been fetched for them" are very different states of health."""

    recommendations_resolved: int = 0
    stances_resolved: int = 0
    players_fetched: int = 0
    # Rows whose gameweek is in the past but whose player has no history we
    # can reach. Counted so silence is never mistaken for completeness.
    unresolvable: int = 0

    @property
    def resolved(self) -> int:
        return self.recommendations_resolved + self.stances_resolved


# element-summary is one HTTP call PER PLAYER, and minutes.py is explicit that
# it must never be fanned out pool-wide. A backfill is exactly where that rule
# is easiest to break by accident: measured on 2026-08-30, 918 unresolved
# stances named 114 distinct players against 16 we held history for, so an
# unbounded catch-up would have fired ~98 calls in one run -- and several
# hundred later in a season, at an API with no published rate limit.
#
# So a run is bounded and the backlog drains across several. That is the same
# shape ADR-0002 already chose for ingestion: a CLI the user runs, which makes
# progress every time rather than doing everything at once. Rows are taken
# oldest-gameweek-first so the drain order is deterministic and the oldest
# history -- the part most at risk of being lost -- is recovered first.
_MAX_FETCHES_PER_RUN = 60

_PENDING_RECOMMENDATIONS = """
SELECT id, gameweek, player_id AS element_id FROM recommendation_log
WHERE outcome_recorded_at IS NULL AND player_id IS NOT NULL AND gameweek IS NOT NULL
  AND gameweek <= %s
ORDER BY gameweek
"""

_PENDING_STANCES = """
SELECT id, gameweek, fpl_player_id AS element_id FROM creator_stances
WHERE outcome_recorded_at IS NULL AND fpl_player_id IS NOT NULL AND gameweek IS NOT NULL
  AND gameweek <= %s
ORDER BY gameweek
"""

# Everything known about one player's season, fetched once per backfill rather
# than per row: a single popular player can carry dozens of unresolved stances,
# and asking the same question of the database once per stance is the whole
# cost of the feature.
_HISTORY = """
SELECT fpl_player_id, gameweek, total_points, value
FROM player_gameweek_history
WHERE fpl_player_id = ANY(%s)
ORDER BY fpl_player_id, gameweek
"""


def _load_histories(conn: Connection, element_ids: list[int]) -> dict[int, list[dict]]:
    if not element_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_HISTORY, (element_ids,))
        rows = cur.fetchall()
    histories: dict[int, list[dict]] = {}
    for row in rows:
        histories.setdefault(row["fpl_player_id"], []).append(row)
    return histories


def resolve_outcome(history: list[dict], gameweek: int) -> tuple[float | None, int | None]:
    """(price change, points) for one player in one gameweek, from their own
    per-gameweek history.

    **Points.** The sum over that gameweek's rows -- a double gameweek is two
    fixtures and both count. A player with history reaching past the gameweek
    but no row in it did not feature, which is a real 0 rather than a gap.

    **Price change.** The move that FOLLOWED the advice: the next recorded
    price minus the price at the time. `value` is the price when that
    gameweek locked, so `value(next) - value(gameweek)` is what happened
    across it. None when nothing later is on record yet -- at the season's
    end, or before the next gameweek has been fetched -- because a price
    movement with no later reading is unknown, not zero.

    Returns (None, None) when the player has no history reaching this
    gameweek at all: unknown, and the caller leaves the row alone.
    """
    if not history:
        return None, None

    reach = max(row["gameweek"] for row in history)
    if reach < gameweek:
        # Nothing fetched this far forward -- we cannot tell "did not feature"
        # from "not looked up yet", and the second must not be scored as a nil.
        return None, None

    at = [row for row in history if row["gameweek"] == gameweek]
    points = sum((row["total_points"] or 0) for row in at) if at else 0

    price_now = next(
        (row["value"] for row in reversed(history) if row["gameweek"] <= gameweek and row["value"] is not None),
        None,
    )
    price_next = next(
        (row["value"] for row in history if row["gameweek"] > gameweek and row["value"] is not None),
        None,
    )
    change = (
        round(float(price_next) - float(price_now), 1)
        if price_now is not None and price_next is not None
        else None
    )
    return change, points


def backfill_outcomes(
    conn: Connection, client=None, through_gameweek: int | None = None
) -> OutcomeBackfill:
    """Resolve every outstanding outcome that can be resolved, however old.

    This is the catch-up the ticket exists for: run it after skipping three
    gameweeks and all three gameweeks' rows resolve, each against its own
    gameweek's numbers. It is driven entirely by which rows lack an outcome,
    so it neither knows nor cares what this ingestion cycle happened to touch.

    `client` is optional. With one, a player named by an unresolved row whose
    history we do not hold is fetched on demand -- which is what makes a row
    from before the minutes cache existed resolvable at all. Without one, the
    backfill still resolves everything already cached and reports the rest as
    unresolvable rather than pretending.
    """
    through = through_gameweek if through_gameweek is not None else latest_finished_gameweek(conn)
    if through is None:
        return OutcomeBackfill()

    with conn.cursor() as cur:
        cur.execute(_PENDING_RECOMMENDATIONS, (through,))
        recommendations = cur.fetchall()
        cur.execute(_PENDING_STANCES, (through,))
        stances = cur.fetchall()

    pending = recommendations + stances
    if not pending:
        return OutcomeBackfill()

    element_ids = sorted({row["element_id"] for row in pending})
    histories = _load_histories(conn, element_ids)

    # Fetch only for players we actually cannot answer for, and only once each.
    fetched = 0
    if client is not None:
        # Imported here rather than at module scope: `fpl_coach.ingestion`'s
        # package __init__ imports the pipeline, which imports this module, so
        # a top-level import is a cycle. Deferring it also keeps this module
        # importable with no ingestion dependency at all, which is what lets
        # the client-less path above stay usable from anywhere.
        from fpl_coach.ingestion.minutes import ensure_player_minutes

        # First-appearance order, not sorted by id: `pending` is already
        # ordered oldest gameweek first, and sorting would throw that away and
        # drain the backlog in an order with no meaning.
        needed: list[int] = []
        for row in pending:
            element_id = row["element_id"]
            if element_id in needed:
                continue
            if resolve_outcome(histories.get(element_id, []), row["gameweek"])[1] is None:
                needed.append(element_id)
        needed = needed[:_MAX_FETCHES_PER_RUN]

        for element_id in needed:
            try:
                ensure_player_minutes(conn, client, element_id, through)
                fetched += 1
            except Exception:
                # One player's history failing must not abort the catch-up --
                # the other rows are still resolvable, and this one stays NULL,
                # which is exactly what it was before.
                continue
        if needed:
            histories = _load_histories(conn, element_ids)

    resolved_recommendations = _apply(
        conn, "recommendation_log", recommendations, histories
    )
    resolved_stances = _apply(conn, "creator_stances", stances, histories)

    unresolvable = len(pending) - resolved_recommendations - resolved_stances
    if resolved_recommendations or resolved_stances:
        conn.commit()

    return OutcomeBackfill(
        recommendations_resolved=resolved_recommendations,
        stances_resolved=resolved_stances,
        players_fetched=fetched,
        unresolvable=unresolvable,
    )


def _apply(
    conn: Connection, table: str, rows: list[dict], histories: dict[int, list[dict]]
) -> int:
    updated = 0
    for row in rows:
        change, points = resolve_outcome(histories.get(row["element_id"], []), row["gameweek"])
        if points is None:
            continue
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET outcome_price_change = %s, outcome_points = %s, "  # noqa: S608
                "outcome_recorded_at = now() WHERE id = %s",
                (change, points, row["id"]),
            )
        updated += 1
    return updated
