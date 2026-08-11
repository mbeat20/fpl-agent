from fpl_coach.models import Player, RecommendationPick, Team
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.reasoning.fake import FakeReasoning


def test_fake_reasoning_is_deterministic():
    team = Team(
        id=1,
        name="Test Team",
        players=[
            Player(id=1, name="Low Price", position="MID", club="X", price=5.0),
            Player(id=2, name="High Price", position="FWD", club="Y", price=9.0),
        ],
    )
    tool = ToolSpec(
        name="get_my_team", description="", input_schema={}, handler=lambda: team
    )
    reasoning = FakeReasoning()

    first = reasoning.reason("Who should I captain?", [tool])
    second = reasoning.reason("Who should I captain?", [tool])

    assert first.pick == second.pick == RecommendationPick(player_id=2, player_name="High Price")
    assert first.alternative.player_id == 1
