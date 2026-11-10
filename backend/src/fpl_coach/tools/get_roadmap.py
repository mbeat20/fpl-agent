"""Issue 27: let the reasoning layer read the Roadmap the user is following.

Without this the model cannot answer a question *about* the plan. Asked "why
am I rolling in GW5?", it had no way to see that a plan existed, so the only
thing it could do was build a new one -- which is exactly the behaviour ticket
27's last open criterion rules out. A step cannot be revised without first
being read.

Read-only and free: `get_live_roadmap` is a single row (ADR-0015). Returning
None when nothing is stored is the normal case for a user who has never asked
for a multi-week plan, not an error.
"""

from psycopg import Connection

from fpl_coach.models import Roadmap
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.roadmap import get_live_roadmap


def get_roadmap(conn: Connection, team_id: int) -> dict:
    """The live Roadmap as a plain dict, or a stated absence.

    The shape deliberately mirrors what `revise_plan_step` expects back: each
    step is listed with its gameweek and both player ids, so the model can
    name the step it means without inventing an identifier. `revisable` says
    so explicitly -- a model that can see a plan but does not know it may
    change one step of it will regenerate the whole thing out of caution.
    """
    roadmap: Roadmap | None = get_live_roadmap(conn, team_id)
    if roadmap is None:
        return {
            "roadmap": None,
            "note": (
                "No roadmap is stored. Nothing to revise -- answer with "
                "submit_plan if the user wants a multi-week plan."
            ),
        }

    return {
        "version": roadmap.version,
        "status": roadmap.status,
        "created_at": roadmap.created_at,
        "horizon": roadmap.plan.horizon,
        "confidence": roadmap.plan.confidence,
        "steps": [
            {
                "gameweek": step.gameweek,
                "decision": step.decision,
                "pick_player_id": step.pick.player_id if step.pick else None,
                "pick_player_name": step.pick.player_name if step.pick else None,
                "out_player_id": step.player_out.player_id if step.player_out else None,
                "out_player_name": step.player_out.player_name if step.player_out else None,
                "evidence": [item.text for item in step.evidence],
                "risks": step.risks,
            }
            for step in sorted(roadmap.plan.steps, key=lambda s: s.gameweek)
        ],
        "revisable": (
            "To change ONE step, call revise_plan_step with that step's gameweek. "
            "The rest of the plan is kept as it is and a new version is stored. "
            "Only rebuild the whole plan with submit_plan when the user's question "
            "genuinely invalidates more than one step."
        ),
    }


def make_get_roadmap_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_roadmap",
        description=(
            "The multi-week plan the user is currently FOLLOWING, with every step's "
            "gameweek, decision, and the ids of the players coming in and going out. "
            "Call this before answering any question about 'the plan', 'my roadmap', "
            "or a specific future gameweek -- the user is asking about this stored "
            "plan, not asking for a new one. Returns nothing when no plan is stored."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_roadmap(conn, team_id),
    )
