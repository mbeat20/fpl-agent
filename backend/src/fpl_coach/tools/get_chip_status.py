"""Issue 20: which chips the user has left.

`DecisionKind` has included `"chip"` since ticket 01 and `fpl_rules.py`
describes all four chips in the system prompt, but nothing in the codebase
knew which were still available -- so "should I wildcard?" could only ever be
answered by inference. This closes that.
"""

from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek
from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.models import ChipStatus
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.squad_state import chip_status


def get_chip_status(conn: Connection, client: FPLClient, team_id: int) -> ChipStatus:
    """The chip catalogue comes from bootstrap rather than a hardcoded list:
    FPL issues a separate set for each half of the season, each with its own
    start/stop gameweek, and has changed the chip rules between seasons more
    than once. Reading the catalogue means a rule change lands automatically
    instead of silently telling the user they hold a chip that no longer
    exists."""
    catalogue = client.get_bootstrap().get("chips", [])
    return chip_status(conn, team_id, catalogue, planning_gameweek(conn, team_id))


def make_get_chip_status_tool(conn: Connection, client: FPLClient, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_chip_status",
        description=(
            "Returns which FPL chips the user has already played and which they still "
            "hold. `used` lists each played chip and the gameweek it was played in. "
            "`remaining` lists each unplayed chip with the gameweek window it can be "
            "used in, and playable_now is true only for chips usable in the gameweek "
            "being planned. `playable_now` at the top level is the shortlist of names.\n\n"
            "Call this before advising on, or ruling out, any chip -- never assume a "
            "chip is available. FPL issues a fresh wildcard, free hit, bench boost and "
            "triple captain for each half of the season, so the same chip name can "
            "appear twice with different windows; use the windows, not the name alone. "
            "If a chip the user asks about is not in `remaining`, say plainly that it "
            "has already been played and when."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_chip_status(conn, client, team_id),
    )
