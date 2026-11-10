"""Ticket 27: revising ONE step of a plan, leaving the rest alone.

Pure -- `merge_step` takes a plan and gives back a plan. The database side
(validating the merged plan, storing it as a new roadmap version) is covered
in the roadmap tests.
"""

import pytest

from fpl_coach.models import Evidence, PlanStep, RecommendationPick, TransferPlan
from fpl_coach.plan_rules import StepNotRevisable, merge_step


def step(gameweek: int, decision: str = "roll", note: str = "original") -> PlanStep:
    return PlanStep(
        gameweek=gameweek,
        decision=decision,
        evidence=[Evidence(text=note)],
        risks=[f"risk for GW{gameweek}"],
    )


def plan(*steps: PlanStep) -> TransferPlan:
    return TransferPlan(horizon="multi-week", confidence=0.7, steps=list(steps))


class TestReplacesOneStep:
    def test_the_named_step_is_the_only_one_that_changes(self):
        original = plan(step(3), step(4), step(5))
        revised = merge_step(original, 4, step(4, note="revised"))

        assert [s.gameweek for s in revised.steps] == [3, 4, 5]
        assert revised.steps[1].evidence[0].text == "revised"
        assert [s.evidence[0].text for s in revised.steps] == ["original", "revised", "original"]

    def test_every_other_step_is_carried_through_untouched(self):
        """The criterion this serves. Regenerating a five-week plan to change
        week four loses the reasoning behind the other four and silently
        changes steps the user never questioned."""
        original = plan(step(3), step(4), step(5))
        revised = merge_step(original, 4, step(4, note="revised"))

        assert revised.steps[0] is original.steps[0]
        assert revised.steps[2] is original.steps[2]

    def test_the_stored_plan_is_not_mutated(self):
        """A Roadmap is versioned and never overwritten (ADR-0016), so the
        version being revised has to survive intact to remain the thing the
        new version is compared against."""
        original = plan(step(3), step(4))
        merge_step(original, 4, step(4, decision="transfer", note="revised"))

        assert original.steps[1].decision == "roll"
        assert original.steps[1].evidence[0].text == "original"

    def test_position_is_preserved_not_appended(self):
        original = plan(step(5), step(3), step(4))
        revised = merge_step(original, 3, step(3, note="revised"))

        assert [s.gameweek for s in revised.steps] == [5, 3, 4]

    def test_horizon_and_confidence_survive(self):
        original = plan(step(3), step(4))
        revised = merge_step(original, 3, step(3, note="revised"))

        assert revised.horizon == "multi-week"
        assert revised.confidence == 0.7

    def test_a_revised_step_can_carry_players(self):
        original = plan(step(3), step(4))
        replacement = PlanStep(
            gameweek=4,
            decision="transfer",
            pick=RecommendationPick(player_id=411, player_name="Haaland"),
            player_out=RecommendationPick(player_id=496, player_name="Kinsky"),
            evidence=[Evidence(text="revised")],
            risks=[],
        )
        revised = merge_step(original, 4, replacement)

        assert revised.steps[1].pick.player_id == 411
        assert revised.steps[1].player_out.player_id == 496


class TestRefusesRatherThanGuessing:
    def test_a_gameweek_with_no_step(self):
        """Could plausibly be treated as an insertion. Refused instead --
        that would edit the user's stored plan into something they did not
        ask for. The message names the gameweeks that do exist so the model
        can correct itself on its next turn."""
        original = plan(step(3), step(4))
        with pytest.raises(StepNotRevisable) as exc:
            merge_step(original, 9, step(9))

        assert "GW9" in str(exc.value)
        assert "[3, 4]" in str(exc.value)

    def test_a_gameweek_with_two_steps(self):
        """`submit_plan` explicitly allows a gameweek to hold two steps (a
        free transfer plus a hit). Revising "the GW4 step" is then ambiguous,
        and answering by revising the first would be a silent wrong guess."""
        original = plan(step(3), step(4), step(4))
        with pytest.raises(StepNotRevisable) as exc:
            merge_step(original, 4, step(4, note="revised"))

        assert "ambiguous" in str(exc.value)
        assert "submit_plan" in str(exc.value)

    def test_the_refusal_leaves_the_plan_alone(self):
        original = plan(step(3), step(4))
        with pytest.raises(StepNotRevisable):
            merge_step(original, 9, step(9))

        assert [s.gameweek for s in original.steps] == [3, 4]
