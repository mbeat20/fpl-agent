"""Issue 20: per-fixture minutes, the expensive tier of minutes security.

`element-summary/{id}/` is one HTTP call per player. Fanning it out across
bootstrap's ~700 elements would turn a ~30-second ingestion into a multi-minute
one and hammer an API with no published rate limit, so this is deliberately
never pool-wide. Two entry points only:

- `refresh_squad_minutes` -- the 15 players the user actually owns, on ingest.
- `ensure_player_minutes` -- one player, on demand, when the user asks about
  someone outside the squad.

**The cache rule.** A player's history is refetched only when the cache is
behind the newest finished gameweek, driven off "rows missing the derived
field" rather than off what changed in this run
([[project_ingestion_backfill_pattern]]). A player who did not feature in a
gameweek has no history row for it at all, so "did we already fetch through
gameweek N" cannot be answered by looking for a row at N -- it is tracked by
the highest gameweek any row for that player reaches, compared against the
latest finished gameweek. That is why a blank/benched week does not cause a
refetch every single cycle.
"""

from psycopg import Connection

from fpl_coach.ingestion.fpl_client import FPLClient


def _clubs_by_id(conn: Connection) -> dict[int, str]:
    """Issue 21: FPL team id -> short name, from the ingested `clubs` table.

    element-summary reports an opponent as a bare team id, and until the clubs
    table existed nothing outside run_ingestion (which holds the map in memory
    while it works) could resolve one -- so the on-demand path stored NULL
    rather than a wrong name. Now it can resolve it, at the cost of one small
    query per call rather than per row.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, short_name FROM clubs")
        return {row["id"]: row["short_name"] for row in cur.fetchall()}


def _cached_through(conn: Connection, element_ids: list[int]) -> dict[int, int]:
    """Highest gameweek we hold any history row for, per player."""
    if not element_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fpl_player_id, MAX(gameweek) AS through FROM player_gameweek_history "
            "WHERE fpl_player_id = ANY(%s) GROUP BY fpl_player_id",
            (element_ids,),
        )
        return {row["fpl_player_id"]: row["through"] for row in cur.fetchall()}


def _price(entry: dict) -> float | None:
    """Issue 24: FPL reports a gameweek's price in tenths (95 = GBP 9.5m).

    Stored in millions to match `players.price`, so nothing downstream has to
    remember which unit a given column is in -- the tenths/millions confusion
    is exactly what made `value` read as a team value in ticket 20.
    """
    value = entry.get("value")
    return None if value is None else value / 10


# How long a value-less row waits before it is worth asking about again.
#
# The bound is the whole point. "Refetch whenever a row has no price" sounds
# right and does not terminate: `_store` writes `value = EXCLUDED.value`, so if
# element-summary does not supply one the row is rewritten as NULL and the
# player is stale again on the very next call -- an unbounded HTTP loop, in a
# module whose entire premise is that these calls are expensive. A test caught
# it (`test_minutes_are_fetched_once_then_served_from_cache` refetched instead
# of serving from cache).
#
# A day is chosen so the real case -- rows written before the column existed --
# is repaired on the next run, while a player FPL genuinely reports no price
# for costs one call a day rather than one per run forever.
_VALUE_RETRY_AFTER = "1 day"


def _missing_value(conn: Connection, element_ids: list[int]) -> set[int]:
    """Players holding a price-less history row we have not asked about lately."""
    if not element_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT fpl_player_id FROM player_gameweek_history "
            "WHERE fpl_player_id = ANY(%s) AND value IS NULL "
            f"AND fetched_at < now() - interval '{_VALUE_RETRY_AFTER}'",
            (element_ids,),
        )
        return {row["fpl_player_id"] for row in cur.fetchall()}


def _store(conn: Connection, element_id: int, history: list[dict], clubs_by_id: dict) -> int:
    rows = [
        (
            element_id,
            entry["round"],
            entry["minutes"],
            entry.get("starts"),
            entry.get("total_points"),
            entry.get("was_home"),
            clubs_by_id.get(entry.get("opponent_team")),
            _price(entry),
        )
        for entry in history
        if entry.get("round") is not None
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO player_gameweek_history "
            "(fpl_player_id, gameweek, minutes, starts, total_points, was_home, opponent, value) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (fpl_player_id, gameweek) DO UPDATE SET "
            "minutes = EXCLUDED.minutes, starts = EXCLUDED.starts, "
            "total_points = EXCLUDED.total_points, was_home = EXCLUDED.was_home, "
            # Issue 21: opponent was missing from this SET, so a row first
            # written with a NULL opponent (the pre-clubs-table on-demand
            # path) could never be repaired by a later fetch. It now heals
            # itself the next time the player's cache goes stale. Issue 24
            # adds `value` on the same basis -- rows written before that
            # column existed backfill themselves rather than staying NULL.
            "opponent = EXCLUDED.opponent, value = EXCLUDED.value, fetched_at = now()",
            rows,
        )
    return len(rows)


def refresh_squad_minutes(
    conn: Connection,
    client: FPLClient,
    element_ids: list[int],
    latest_finished_gameweek: int | None,
    clubs_by_id: dict | None = None,
) -> int:
    """Fetches per-fixture history for squad players whose cache is stale.

    Returns the number of players actually fetched, so ingestion can report
    the cost rather than hiding it. A player already cached through the latest
    finished gameweek is skipped entirely -- on a normal second run in the
    same gameweek this makes zero HTTP calls.
    """
    if latest_finished_gameweek is None or not element_ids:
        return 0

    # Ingestion passes its own in-memory map (it already has one and would
    # otherwise re-query for data it is holding); every other caller gets it
    # from the clubs table rather than storing a NULL opponent.
    if clubs_by_id is None:
        clubs_by_id = _clubs_by_id(conn)

    cached = _cached_through(conn, element_ids)
    # Issue 24: "missing the derived field" counts as stale, not just "behind
    # the calendar" ([[project_ingestion_backfill_pattern]]). Rows written
    # before `value` existed are cached through the latest gameweek and would
    # otherwise never be refetched, so the column they lack would stay NULL
    # for exactly the players already on record -- the same trap ticket 06's
    # embedding backfill hit.
    incomplete = _missing_value(conn, element_ids)
    stale = [
        element_id
        for element_id in element_ids
        if cached.get(element_id) is None
        or cached[element_id] < latest_finished_gameweek
        or element_id in incomplete
    ]

    fetched = 0
    for element_id in stale:
        try:
            summary = client.get_element_summary(element_id)
        except Exception:
            # One player's history failing must not abort a whole ingestion
            # run -- minutes are an enrichment, not the squad itself.
            continue
        _store(conn, element_id, summary.get("history", []), clubs_by_id)
        fetched += 1
    conn.commit()
    return fetched


def ensure_player_minutes(
    conn: Connection,
    client: FPLClient,
    element_id: int,
    latest_finished_gameweek: int | None,
    clubs_by_id: dict | None = None,
) -> None:
    """On-demand equivalent for a single player outside the squad."""
    refresh_squad_minutes(
        conn, client, [element_id], latest_finished_gameweek, clubs_by_id
    )


def recent_minutes(conn: Connection, element_id: int, limit: int = 5) -> list[dict]:
    """The player's last `limit` gameweeks of minutes, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gameweek, minutes, starts, total_points, opponent, was_home "
            "FROM player_gameweek_history WHERE fpl_player_id = %s "
            "ORDER BY gameweek DESC LIMIT %s",
            (element_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
