from fpl_coach.models import Alternative, Recommendation, RecommendationPick
from fpl_coach.reasoning.base import ToolSpec


class FakeReasoning:
    """Deterministic Reasoning test double — no network calls.

    Picks the squad's most expensive player as captain and the next most
    expensive as the alternative, tie-broken by player id, so repeated
    calls against identical seeded data always return the identical
    primary pick (ADR-0004). Used by Seam-1 tests.
    """

    def __init__(self) -> None:
        self.last_system = ""

    def reason(self, prompt: str, tools: list[ToolSpec], system: str = "") -> Recommendation:
        self.last_system = system
        get_my_team = next(t for t in tools if t.name == "get_my_team")
        team = get_my_team.handler()
        players = sorted(team.players, key=lambda p: (-p.price, p.id))
        top, runner_up = players[0], players[1]

        return Recommendation(
            pick=RecommendationPick(player_id=top.id, player_name=top.name),
            confidence=0.75,
            evidence=[f"{top.name} is the highest-priced player in the squad."],
            risks=[
                "Placeholder heuristic only — no fixture or form data yet (issues 03/05)."
            ],
            alternative=Alternative(
                player_id=runner_up.id,
                player_name=runner_up.name,
                reason=f"{runner_up.name} is the next-highest-priced option.",
            ),
        )
