"""One answer to "which gameweek are we planning for", shared by everything
that must not act on a finished gameweek.

Two sources, neither sufficient alone:

- `gameweek_state` (instincts.current_gameweek) is what ingestion resolved.
  It is authoritative when ingestion is current, but it only moves when
  ingestion runs (ADR-0002: no scheduler), so it falls behind the calendar.
- `fixtures.kickoff_time` cannot fall behind -- it is wall-clock -- but it
  says nothing about a team's own state.

Taking the later of the two means a stale `gameweek_state` can never pin
advice to a gameweek whose fixtures have all been played, which is the
failure the user saw as the assistant confidently recommending a captain for
a fixture that had already happened.
"""

from datetime import datetime, timezone

from psycopg import Connection

from fpl_coach.fixtures import upcoming_gameweek
from fpl_coach.instincts import current_gameweek


def planning_gameweek(conn: Connection, team_id: int) -> int | None:
    """The gameweek advice should target, or None when neither source knows
    one yet (no ingestion has ever run and no fixtures are loaded)."""
    persisted = current_gameweek(conn, team_id)
    upcoming = upcoming_gameweek(conn)
    if persisted is None:
        return upcoming
    if upcoming is None:
        return persisted
    return max(persisted, upcoming)


# ---------------------------------------------------------------------------
# Issue 19: the gameweek calendar.
#
# planning_gameweek above answers "which gameweek is it now". What follows
# answers "which gameweek was this piece of content published FOR", which is a
# different question and needs a different input: the deadline, not the
# kickoff and not the state snapshot.
#
# A deadline is the right boundary because it is the moment the manager stops
# being able to act. Everything a creator publishes in the run-up to it is
# preview for that gameweek; the instant it passes, the same creator's next
# post is about the following one. Bucketing on anything else -- fetched_at,
# gameweek_state, the run's planning gameweek -- puts deadline-eve opinion in
# the gameweek that has already been played, which is exactly the mis-tagging
# ticket 19 must not inherit (591 stances from the 2026-08-27 batch).

_DEADLINES_SQL = "SELECT id, deadline_time FROM gameweeks ORDER BY deadline_time"

# Fallback when the gameweeks table has not been populated yet -- i.e. no
# run_ingestion has happened since ticket 19 added it. A gameweek's deadline
# is roughly 90 minutes before its first kickoff, and the fixtures table is
# already ingested, so the first kickoff is a usable proxy boundary. It is
# wrong only for content published inside that 90-minute window, which is
# after the deadline and therefore un-actionable either way.
_FIRST_KICKOFF_SQL = """
SELECT gameweek AS id, MIN(kickoff_time) AS deadline_time
FROM fixtures
WHERE gameweek IS NOT NULL AND kickoff_time IS NOT NULL
GROUP BY gameweek
ORDER BY MIN(kickoff_time)
"""

# What load_deadlines actually found, reported alongside the numbers rather
# than left implicit -- a consensus count means something different when the
# boundary is a proxy, and "none" is the case where the caller must fall back
# to the stored (unreliable) tag rather than silently return zeros.
DeadlineSource = str  # "deadlines" | "first_kickoff" | "none"


def load_deadlines(conn: Connection) -> tuple[list[tuple[int, datetime]], DeadlineSource]:
    """[(gameweek, deadline)] in time order, plus where they came from.

    Read once per request and passed around, rather than queried per row: a
    consensus count walks every Stance a player has, and 1,300 round trips to
    ask the same 38-row question would be the whole cost of the feature.
    """
    with conn.cursor() as cur:
        cur.execute(_DEADLINES_SQL)
        rows = cur.fetchall()
    if rows:
        return [(row["id"], row["deadline_time"]) for row in rows], "deadlines"

    with conn.cursor() as cur:
        cur.execute(_FIRST_KICKOFF_SQL)
        rows = cur.fetchall()
    if rows:
        return [(row["id"], row["deadline_time"]) for row in rows], "first_kickoff"

    return [], "none"


def gameweek_at(
    deadlines: list[tuple[int, datetime]], when: datetime | None
) -> int | None:
    """The gameweek `when` falls in the run-up to: the first one whose
    deadline it precedes.

    None for undated content, and None for content published after the last
    known deadline -- there is no gameweek left to have been talking about,
    and inventing one would file real opinion under a gameweek that does not
    exist.
    """
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    for gameweek, deadline in deadlines:
        if deadline > when:
            return gameweek
    return None


def next_deadline(
    deadlines: list[tuple[int, datetime]], now: datetime | None = None
) -> tuple[int, datetime] | None:
    """The next deadline the user can still act before, or None once the
    season's last one has passed."""
    now = now or datetime.now(timezone.utc)
    for gameweek, deadline in deadlines:
        if deadline > now:
            return gameweek, deadline
    return None
