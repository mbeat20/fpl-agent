"""The ingestion run ledger: recording refreshes, and answering how stale the
data is when nothing recorded one.

ADR-0002 keeps ingestion on demand and this does not change that -- a run
still only happens because someone asked for one. What it adds is a record of
having asked, which the app had no way to produce before. The only traces a
run used to leave were `gameweek_state.updated_at` and
`max(creator_content.fetched_at)`, and both are side effects rather than
records: a run that fetched nothing new moves neither, and a run that died
half way leaves both looking exactly like a success.

Two rules are worth reading before changing anything here.

**A `running` row is a claim, not a label.** `ingestion_runs_one_running_per_kind`
is a partial unique index, so a second concurrent run of the same kind is a
constraint violation rather than a race -- which is the difference between a
double-clicked refresh button costing one Grok bill and costing two. That
means `start_run` refusing is the normal, expected path, and callers must
handle `RunAlreadyRunning` rather than treat it as an error.

**Freshness never guesses.** `freshness` reports where its number came from
(`run` / `proxy` / `none`), the same way `load_deadlines` reports whether it
found real deadlines or fell back to first kickoffs. `none` is a real answer
-- nothing has been ingested -- and is not the same as old. Rendering it as
an age would invent a refresh that never happened, which is exactly the
fails-open shape that has bitten this codebase three times.
"""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from fpl_coach.models import DataFreshness, IngestionKind, IngestionRun, IngestionStatus

KINDS: tuple[IngestionKind, ...] = ("fpl", "creators")

# What to call each kind in a sentence a person reads. Defined here rather
# than in the front end because these strings appear in API error details too,
# and two copies would drift.
LABELS: dict[IngestionKind, str] = {"fpl": "An FPL", "creators": "A creator"}

# How long a `running` row is believed before it is treated as abandoned.
#
# It has to exceed the longest run that could legitimately still be working. A
# creator run walks ~19 creators through Grok's X Search, up to three videos
# each through transcript fetching that YouTube rate-limits hard, and every
# new chunk through Voyage -- tens of minutes is normal, not pathological.
#
# The cost of setting it too low is worse than too high: reaping a live run
# frees the lock, and the next refresh starts a SECOND concurrent run of a
# billed pipeline. Too high merely means a crashed run blocks refreshes for
# an hour, which is visible and self-healing.
ABANDON_AFTER = timedelta(hours=2)

# When to say the data is old enough to caveat what rests on it.
#
# One gameweek, roughly. FPL data older than that is describing a different
# set of fixtures; creator opinion older than that is about a deadline that
# has passed, which is the same reason `conversations` and `instincts` are
# gameweek-scoped rather than kept.
STALE_AFTER = timedelta(days=7)

_PROXY_SQL: dict[IngestionKind, str] = {
    # What run_ingestion writes last-ish and unconditionally, so it moves on
    # every successful FPL run even when nothing else changed.
    "fpl": "SELECT updated_at AS at FROM gameweek_state WHERE team_id = %(team_id)s",
    # Not team-scoped: creator content is global (creators are a shared
    # roster, not a per-team subscription), and scoping it to a team would
    # report `none` for the only team there is.
    "creators": "SELECT MAX(fetched_at) AS at FROM creator_content",
}


class RunAlreadyRunning(Exception):
    """A run of this kind is already in flight.

    Carries the existing run so the caller can say which one, and since when,
    rather than just refusing. A refresh button that says "already running,
    started 4 minutes ago" needs no explanation; one that says "conflict"
    invites a second click.
    """

    def __init__(self, run: IngestionRun) -> None:
        self.run = run
        super().__init__(
            f"{LABELS[run.kind]} refresh is already in progress "
            f"(run {run.id}, started {run.started_at})."
        )


def start_run(
    conn: Connection,
    team_id: int,
    kind: IngestionKind,
    trigger: str = "api",
) -> IngestionRun:
    """Claim the lock and return the run to execute.

    Committed before returning, deliberately: the HTTP caller gets the run id
    in its response and the work happens afterwards in the background, so the
    row has to be visible to the status endpoint the moment the response
    lands. An uncommitted claim would let a second request in.
    """
    reap_abandoned(conn, team_id)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_runs (team_id, kind, status, trigger) "
                "VALUES (%s, %s, 'running', %s) "
                "RETURNING id, kind, status, trigger, started_at, finished_at, summary, error",
                (team_id, kind, trigger),
            )
            row = cur.fetchone()
        conn.commit()
    except UniqueViolation:
        # The partial unique index fired: someone else holds the lock. Roll
        # back before reading, or the aborted transaction makes the SELECT
        # fail too and the caller gets an opaque error instead of the useful
        # one.
        conn.rollback()
        existing = _running_run(conn, team_id, kind)
        if existing is None:
            # The holder finished between the violation and this read. Rare,
            # and retrying once is correct rather than clever -- the lock is
            # genuinely free now.
            return start_run(conn, team_id, kind, trigger)
        raise RunAlreadyRunning(existing) from None

    assert row is not None
    return _to_run(row)


def finish_run(conn: Connection, run_id: int, summary: object) -> None:
    """Record success and release the lock.

    `summary` takes the pipeline's own frozen dataclass and stores it whole.
    Reshaping it here would mean this module needing to know what each
    pipeline counts, and both grow a counter per ticket.
    """
    payload = asdict(summary) if is_dataclass(summary) and not isinstance(summary, type) else summary
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status = 'succeeded', finished_at = now(), "
            "summary = %s WHERE id = %s",
            (Jsonb(payload), run_id),
        )
    conn.commit()


def fail_run(conn: Connection, run_id: int, error: str) -> None:
    """Record the failure and release the lock.

    The lock is released on failure as much as on success: a failed refresh is
    the case where the user most wants to press the button again.

    The message is truncated rather than stored whole. A psycopg traceback
    against a Supabase URL can be enormous, and this string is rendered in a
    panel -- it needs to be the first useful line, not the whole stack.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status = 'failed', finished_at = now(), "
            "error = %s WHERE id = %s",
            (error[:2000], run_id),
        )
    conn.commit()


def reap_abandoned(conn: Connection, team_id: int) -> int:
    """Mark long-`running` rows abandoned, returning how many.

    Without this a run killed mid-flight -- a uvicorn reload, a closed laptop
    -- leaves its claim standing forever and every future refresh 409s. That
    failure mode is silent and permanent, which makes it worse than the
    double-run it exists to prevent.

    `abandoned` rather than `failed`: we know the run stopped being watched,
    not that it stopped working, and a killed FPL run may well have written
    most of its data. Saying "failed" would imply a rollback that never
    happened.
    """
    cutoff = datetime.now(timezone.utc) - ABANDON_AFTER
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status = 'abandoned', finished_at = now(), "
            "error = 'Run stopped reporting and was assumed abandoned. Any data it "
            "wrote before stopping stands.' "
            "WHERE team_id = %s AND status = 'running' AND started_at < %s",
            (team_id, cutoff),
        )
        reaped = cur.rowcount
    conn.commit()
    return reaped


def latest_run(conn: Connection, team_id: int, kind: IngestionKind) -> IngestionRun | None:
    """The most recent run of this kind, whatever became of it.

    Newest rather than newest-successful on purpose: a failure that happened
    after the last success is the most important thing on the screen, and
    filtering to successes would hide it behind a stale green stamp.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, trigger, started_at, finished_at, summary, error "
            "FROM ingestion_runs WHERE team_id = %s AND kind = %s "
            "ORDER BY started_at DESC LIMIT 1",
            (team_id, kind),
        )
        row = cur.fetchone()
    return None if row is None else _to_run(row)


def recent_runs(conn: Connection, team_id: int, limit: int = 20) -> list[IngestionRun]:
    """Both kinds interleaved, newest first -- the history a Data page shows."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, trigger, started_at, finished_at, summary, error "
            "FROM ingestion_runs WHERE team_id = %s ORDER BY started_at DESC LIMIT %s",
            (team_id, limit),
        )
        rows = cur.fetchall()
    return [_to_run(row) for row in rows]


def freshness(conn: Connection, team_id: int, kind: IngestionKind) -> DataFreshness:
    """How old this source's data is, and how well we know it.

    Tries the ledger first, then the data's own timestamps, then admits it
    does not know. The middle step is what stops the app claiming it has
    never been refreshed while sitting on a full database -- which is every
    run made before this table existed.
    """
    now = datetime.now(timezone.utc)

    last_success = _last_success_at(conn, team_id, kind)
    source: str = "run"
    if last_success is None:
        last_success = _proxy_at(conn, team_id, kind)
        source = "proxy" if last_success is not None else "none"

    if last_success is None:
        return DataFreshness(
            kind=kind,
            source="none",
            note=(
                "Nothing has been ingested for this source yet, so anything "
                "resting on it is missing rather than out of date."
            ),
        )

    age = (now - last_success).total_seconds()
    stale = age > STALE_AFTER.total_seconds()
    return DataFreshness(
        kind=kind,
        last_success=last_success.isoformat(),
        source=source,  # type: ignore[arg-type]
        age_seconds=age,
        is_stale=stale,
        note=_freshness_note(kind, source, age, stale),
    )


def ingestion_status(conn: Connection, team_id: int) -> IngestionStatus:
    """One read for the whole refresh control.

    Reaps first, so a status poll is what recovers from a crashed run -- the
    user opening the page is the most reliable trigger there is, and it means
    recovery needs no scheduler of its own (which ADR-0002 would have had
    something to say about).
    """
    reap_abandoned(conn, team_id)
    runs = recent_runs(conn, team_id)
    return IngestionStatus(
        runs=runs,
        freshness=[freshness(conn, team_id, kind) for kind in KINDS],
        running=[run.kind for run in runs if run.status == "running"],
    )


# ---------------------------------------------------------------------------
# Internals


def _running_run(conn: Connection, team_id: int, kind: IngestionKind) -> IngestionRun | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, status, trigger, started_at, finished_at, summary, error "
            "FROM ingestion_runs WHERE team_id = %s AND kind = %s AND status = 'running'",
            (team_id, kind),
        )
        row = cur.fetchone()
    return None if row is None else _to_run(row)


def _last_success_at(conn: Connection, team_id: int, kind: IngestionKind) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(finished_at) AS at FROM ingestion_runs "
            "WHERE team_id = %s AND kind = %s AND status = 'succeeded'",
            (team_id, kind),
        )
        row = cur.fetchone()
    return None if row is None else _as_utc(row["at"])


def _proxy_at(conn: Connection, team_id: int, kind: IngestionKind) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(_PROXY_SQL[kind], {"team_id": team_id})
        row = cur.fetchone()
    return None if row is None else _as_utc(row["at"])


def _freshness_note(kind: IngestionKind, source: str, age: float, stale: bool) -> str:
    what = "FPL data" if kind == "fpl" else "Creator content"
    when = _humanise(age)

    if source == "proxy":
        # The distinction matters: this number says when data last CHANGED,
        # so a run that legitimately found nothing new reads as older than it
        # is. Saying so is cheaper than pretending otherwise, and it stops
        # being the answer as soon as one run has been recorded.
        base = (
            f"{what} last changed {when} ago. No ingestion run is on record, so "
            f"that is the newest data rather than the last time a refresh ran -- "
            f"a refresh that found nothing new would not move it."
        )
    else:
        base = f"{what} last refreshed successfully {when} ago."

    if stale:
        return base + " That is over a gameweek old, so treat anything resting on it as out of date."
    return base


def _humanise(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _as_utc(value: datetime | None) -> datetime | None:
    """Postgres hands back tz-aware values for TIMESTAMPTZ, but a naive one
    arrives from anything that wrote through a connection with no timezone
    set. Subtracting a naive from an aware datetime raises, and it would raise
    inside a freshness read -- i.e. it would take down the dashboard to
    report a timestamp."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _to_run(row: dict) -> IngestionRun:
    started = _as_utc(row["started_at"])
    finished = _as_utc(row["finished_at"])
    duration = None if finished is None or started is None else (finished - started).total_seconds()
    return IngestionRun(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        trigger=row.get("trigger", "api"),
        started_at=started.isoformat() if started else "",
        finished_at=finished.isoformat() if finished else None,
        duration_seconds=duration,
        summary=row.get("summary"),
        error=row.get("error"),
    )
