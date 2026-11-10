"""Issue 19: the bandwagon meter. See consensus.py for the reasoning behind
the unit (creators, not Stances) and the emphasis on movement over level.

This module is the plumbing: one query, gameweek attribution from the
calendar, and the Instinct join.
"""

from psycopg import Connection

from fpl_coach.consensus import (
    classify_movement,
    describe,
    instinct_note,
    summarise_window,
)
from fpl_coach.gameweek import gameweek_at, load_deadlines
from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import (
    ConsensusChange,
    ConsensusWindow,
    CreatorConsensus,
    Instinct,
    StanceCount,
)
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.tools.get_instincts import get_instincts

# Every Stance for the player, undated ones included -- the bucketing happens
# in Python against the deadline calendar, not in SQL against the stored
# gameweek tag, which is the whole point of the ticket.
#
# `subject` is matched as well as `player_name`: ticket 16 widened the
# vocabulary so a Stance can be about a club or a chip instead of a player,
# and a club-level "Arsenal's run is the best in the game" is legitimately
# part of the consensus on an Arsenal player when the user names one. It is
# not force-fitted, only matched when the text actually names them.
_CONSENSUS_SQL = """
SELECT s.id AS stance_id, c.name AS creator_name, c.trust_tier, s.stance,
       s.gameweek AS stored_gameweek, cc.published_at
FROM creator_stances s
JOIN creators c ON c.id = s.creator_id
JOIN creator_content cc ON cc.id = s.source_content_id
WHERE s.player_name ILIKE %(pattern)s OR s.subject ILIKE %(pattern)s
"""

_ROSTER_SQL = "SELECT count(*) AS n FROM creators"


def _window(payload: dict) -> ConsensusWindow:
    return ConsensusWindow(
        gameweek=payload["gameweek"],
        creators_covering=payload["creators_covering"],
        creators_positive=payload["creators_positive"],
        creators_negative=payload["creators_negative"],
        creators_neutral=payload["creators_neutral"],
        by_stance=[StanceCount(**item) for item in payload["by_stance"]],
        creator_names=payload["creator_names"],
        stances=payload["stances"],
    )


def _matching_instincts(conn: Connection, team_id: int, player_name: str) -> list[Instinct]:
    """The user's live Instincts that name this player.

    Substring, case-insensitive, on the name as asked about -- an Instinct is
    free text ("thinking Rogers to Enzo"), so there is nothing structured to
    join on. A loose match is the right failure direction here: showing the
    user their own note when it is only tangentially related costs them a
    glance, and failing to show it costs them the whole point of ticket 13.
    """
    needle = player_name.strip().casefold()
    if not needle:
        return []
    return [
        note for note in get_instincts(conn, team_id).notes if needle in note.text.casefold()
    ]


def get_creator_consensus(
    conn: Connection, player_name: str, team_id: int
) -> CreatorConsensus:
    """Counts, week over week, from Stances already stored -- no API call.

    The previous window is `planning - 1`, not "the last gameweek anybody
    said anything in". A gap in coverage is itself the finding
    (`new_coverage` / `no_coverage`), and reaching further back to fill the
    comparison would manufacture a swing out of a quiet fortnight.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    deadlines, source = load_deadlines(conn)

    with conn.cursor() as cur:
        cur.execute(_CONSENSUS_SQL, {"pattern": f"%{player_name}%"})
        rows = cur.fetchall()
        cur.execute(_ROSTER_SQL)
        roster_size = cur.fetchone()["n"]

    # Fall back to the stored tag only when there is no calendar at all --
    # neither deadlines nor fixtures ingested. The tag is known-unreliable
    # (consensus.py), so this path exists to degrade rather than to be used.
    use_stored_tag = not deadlines
    undated = 0
    for row in rows:
        if use_stored_tag:
            row["derived_gameweek"] = row["stored_gameweek"]
        else:
            row["derived_gameweek"] = gameweek_at(deadlines, row["published_at"])
        if row["derived_gameweek"] is None:
            undated += 1

    previous_gameweek = planning - 1 if planning is not None else None
    current_rows = [r for r in rows if r["derived_gameweek"] == planning]
    previous_rows = [r for r in rows if r["derived_gameweek"] == previous_gameweek]

    current = summarise_window(planning, current_rows)
    previous = summarise_window(previous_gameweek, previous_rows)
    movement = classify_movement(current, previous)

    instincts = _matching_instincts(conn, team_id, player_name)

    note = None
    if use_stored_tag:
        note = (
            "No gameweek calendar is loaded, so these counts fall back to each "
            "Stance's stored gameweek tag, which is a snapshot taken when its "
            "ingestion run started and is known to mis-file deadline-eve opinion. "
            "Treat the week-over-week change as indicative only, and run "
            "fpl-ingest to fix it."
        )
    elif source == "first_kickoff":
        note = (
            "Gameweek boundaries are approximated from each gameweek's first "
            "kickoff rather than its actual deadline, because the gameweeks table "
            "has not been populated yet. Only content published inside the ~90 "
            "minutes between deadline and kickoff can be misplaced by this. Run "
            "fpl-ingest to load real deadlines."
        )

    return CreatorConsensus(
        player_name=player_name,
        roster_size=roster_size,
        planning_gameweek=planning,
        current=_window(current),
        previous=_window(previous),
        change=ConsensusChange(
            covering=current["creators_covering"] - previous["creators_covering"],
            positive=current["creators_positive"] - previous["creators_positive"],
            negative=current["creators_negative"] - previous["creators_negative"],
            neutral=current["creators_neutral"] - previous["creators_neutral"],
        ),
        movement=movement,
        summary=describe(player_name, movement, current, previous, roster_size),
        instincts=instincts,
        instinct_note=(
            instinct_note(player_name, movement, current, previous) if instincts else None
        ),
        gameweek_source="stored_tag" if use_stored_tag else source,
        undated_stances=undated,
        note=note,
    )


def make_get_creator_consensus_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_creator_consensus",
        description=(
            "Returns how many of the trusted creators hold each view on one named "
            "player THIS gameweek, the same counts for LAST gameweek, and the change "
            "-- the app's answer to 'am I reassessing, or am I being herded?'. Counts "
            "distinct creators, not posts, so one prolific account cannot look like a "
            "crowd. Call it whenever you are about to argue for or against a specific "
            "player, and read `summary` and `movement` rather than only the numbers: "
            "'settled' means a view held steadily across gameweeks, 'surging' means a "
            "sharp swing inside a week, and those mean opposite things even when the "
            "current counts are identical. "
            "Consensus is evidence ABOUT evidence, never an argument on its own. A "
            "high or fast-rising count must appear in your `risks` -- state plainly "
            "that the move is popular and fast-moving and that popularity is not a "
            "reason -- and must never be the sole basis of an evidence line. A low "
            "count is an absence of coverage, not disagreement. "
            "If `instincts` is non-empty the user logged a note naming this player "
            "earlier in the week: follow `instinct_note` exactly. "
            "This result carries no citation_id -- a count is an aggregate, like a "
            "season xG total, so an evidence line resting on it is correctly uncited. "
            "Use get_creator_opinions when you want to quote and cite what a specific "
            "creator actually said."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "player_name": {"type": "string", "description": "the player's name"}
            },
            "required": ["player_name"],
        },
        handler=lambda player_name: get_creator_consensus(conn, player_name, team_id),
    )
