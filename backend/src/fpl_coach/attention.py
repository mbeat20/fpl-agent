"""Issue 28: which players the discourse is actually on, and what moved.

Ticket 19 built this machinery and then pointed it at a deliberately narrow
target. `consensus.py` computes, for one player, how many distinct creators
cover them, the positive/negative split, the week-over-week delta and a
movement classification; `GET /team/consensus` runs it over only the players
the user has logged an Instinct about. That scoping was right for what ticket
19 was for -- reviewing your own decision -- and it left the app unable to
answer a much more basic question: what is everyone talking about this week?

Measured 2026-08-30, the database held 2,217 stances across 19 creators and
the dashboard surfaced almost none of them unless the user had already named a
player.

Three things this is careful about.

**The derivative is the ordering, not the level.** "Salah, 12 creators" is
true every week and carries no information; "1 -> 7 creators in four days" is
the story. `consensus.py`'s own docstring argues that movement is the signal,
and this is that argument applied to the sort.

**One query, not one per player.** Ticket 19's per-player path issues a query
per player by design, which is correct for a handful of instinct-players and
would be six hundred round trips here. Everything is fetched once and bucketed
in memory.

**It counts creators, never Stances.** Six posts from one account is one
creator. `summarise_window` already enforces that; the board must not quietly
re-count rows on the way past.

What it must not become is a shopping list. Ticket 19 scoped consensus to
instinct-players *specifically* so the bandwagon meter could not be used to go
shopping for bandwagons, and inverting that scoping means carrying the framing
here instead: `preferences.md` treats heavy coverage as a risk rather than an
endorsement, so the board reports what the herd is doing and stops.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from psycopg import Connection

from fpl_coach.consensus import classify_movement, describe, summarise_window
from fpl_coach.gameweek import (
    gameweek_at,
    load_deadlines,
    planning_gameweek as resolve_planning_gameweek,
)
from fpl_coach.models import (
    AttentionBoard,
    AttentionRow,
    ConsensusChange,
    ConsensusWindow,
)

DEFAULT_LIMIT = 12
# What counts as "just happened". Two days spans a normal news cycle -- a
# Friday press conference is still fresh on Saturday morning -- without
# stretching so far that everything in a gameweek qualifies and the
# distinction stops meaning anything.
RECENT_HOURS = 48

# Every player-level stance with the identity needed to place it in a gameweek
# and attribute it to a creator. Fetched once for the whole board: ticket 19's
# per-player query is right for a handful of instinct-players and would be
# hundreds of round trips here.
# Same count ticket 19 uses, so "7 of 19" means the same thing on both
# surfaces.
_ROSTER_SQL = "SELECT count(*) AS n FROM creators"

# `player_name` is what the creator said; `f.name` is who that resolved to.
# Grouping on the resolved player is what stops one man appearing as several
# rows -- "Foden", "Phil Foden" and "Phil Fodenn" were three entries splitting
# one player's creator count between them, which understates exactly the
# movement this board exists to report. An unresolved name still groups on
# itself: two spellings of a player who is not in the pool stay apart, and
# that is the honest answer rather than a guess at which are the same man.
_ALL_STANCES_SQL = """
SELECT s.id AS stance_id, coalesce(f.name, s.player_name) AS player_name,
       c.name AS creator_name, s.stance,
       s.gameweek AS stored_gameweek, cc.published_at
FROM creator_stances s
JOIN creators c ON c.id = s.creator_id
JOIN creator_content cc ON cc.id = s.source_content_id
LEFT JOIN fpl_players f ON f.id = s.fpl_player_id
WHERE coalesce(f.name, s.player_name) IS NOT NULL
  AND coalesce(f.name, s.player_name) <> ''
"""


def _bucket(
    rows: list[dict], deadlines: list, source: str, planning: int | None
) -> dict[str, dict[int | None, list[dict]]]:
    """{player: {gameweek: rows}} using deadline attribution.

    A Stance belongs to the gameweek whose deadline it PRECEDES -- the moment
    the manager stops being able to act on it. `creator_stances.gameweek` is
    the run's own tag and is known-unreliable (ticket 19), so it is a last
    resort used only when no calendar exists at all.
    """
    by_player: dict[str, dict[int | None, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if source == "none":
            gameweek = row["stored_gameweek"]
        else:
            gameweek = gameweek_at(deadlines, row["published_at"]) or row["stored_gameweek"]
        # A stance we cannot place in any gameweek is not silently filed into
        # the current one -- that would inflate exactly the count this board
        # is built to report honestly.
        if gameweek is None:
            continue
        by_player[row["player_name"]][gameweek].append(row)
    return by_player


def _recent(rows: list[dict], now: datetime) -> list[str]:
    """Distinct creators who said something in the last RECENT_HOURS."""
    cutoff = now - timedelta(hours=RECENT_HOURS)
    return sorted(
        {
            row["creator_name"]
            for row in rows
            if row["published_at"] is not None and row["published_at"] >= cutoff
        }
    )


def _rank_key(row: AttentionRow) -> tuple:
    """Biggest mover first.

    Sorted on the ABSOLUTE positive delta: a player seven creators have turned
    against is exactly as newsworthy as one seven have turned towards, and
    ranking only on rises would quietly make the board a buy list. Ties break
    on current coverage, then on name so the order is total and stable.
    """
    return (
        -abs(row.change.positive),
        -abs(row.change.covering),
        -row.current.creators_covering,
        row.player_name,
    )


def build_attention_board(
    conn: Connection,
    team_id: int,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> AttentionBoard:
    now = now or datetime.now(timezone.utc)
    planning = resolve_planning_gameweek(conn, team_id)
    deadlines, source = load_deadlines(conn)
    with conn.cursor() as cur:
        cur.execute(_ROSTER_SQL)
        roster = cur.fetchone()["n"]

    if planning is None:
        return AttentionBoard(
            roster_size=roster,
            gameweek_source=source,
            note=(
                "No gameweek is known yet, so there is nothing to measure coverage "
                "against. Run fpl-ingest."
            ),
        )

    with conn.cursor() as cur:
        cur.execute(_ALL_STANCES_SQL)
        rows = cur.fetchall()

    by_player = _bucket(rows, deadlines, source, planning)

    board: list[AttentionRow] = []
    for player_name, gameweeks in by_player.items():
        current_rows = gameweeks.get(planning, [])
        previous_rows = gameweeks.get(planning - 1, [])
        if not current_rows and not previous_rows:
            continue

        current = summarise_window(planning, current_rows)
        previous = summarise_window(planning - 1, previous_rows)
        movement = classify_movement(current, previous)
        recent = _recent(current_rows, now)

        board.append(
            AttentionRow(
                player_name=player_name,
                current=ConsensusWindow(**current),
                previous=ConsensusWindow(**previous),
                change=ConsensusChange(
                    covering=current["creators_covering"] - previous["creators_covering"],
                    positive=current["creators_positive"] - previous["creators_positive"],
                    negative=current["creators_negative"] - previous["creators_negative"],
                    neutral=current["creators_neutral"] - previous["creators_neutral"],
                ),
                movement=movement,
                creators_last_48h=len(recent),
                creator_names_last_48h=recent,
                # The same sentence ticket 19 writes for a single player, so
                # the board and the panel never describe the same movement two
                # different ways.
                summary=describe(player_name, movement, current, previous, roster),
            )
        )

    board.sort(key=_rank_key)
    notes = []
    if source == "first_kickoff":
        notes.append(
            "Gameweeks were attributed from each gameweek's first kickoff rather than "
            "its deadline, because the gameweeks calendar has not been ingested. "
            "Content published in the ~90 minutes between the two is filed one "
            "gameweek late."
        )
    elif source == "none":
        notes.append(
            "No gameweek calendar or fixtures are ingested, so these counts fall back "
            "to the gameweek each ingestion run was tagged with -- known to be "
            "unreliable. Treat the movement as indicative only."
        )

    return AttentionBoard(
        planning_gameweek=planning,
        rows=board[:limit],
        total_players=len(board),
        returned=len(board[:limit]),
        roster_size=roster,
        gameweek_source=source,
        note=" ".join(notes) or None,
    )
