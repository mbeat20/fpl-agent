"""Seam-2: exercises the real Claude-backed Reasoning implementation.

Excluded from the default run (see [tool.pytest.ini_options] addopts in
pyproject.toml). Requires ANTHROPIC_API_KEY. Run explicitly with:

    uv run pytest -m integration
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.app import app
from fpl_coach.preferences import build_system_prompt, load_preferences
from fpl_coach.reasoning import ClaudeReasoning
from fpl_coach.tools import make_compare_players_tool, make_get_my_team_tool, make_get_player_tool

pytestmark = pytest.mark.integration

NON_SQUAD_PLAYER_ID = 921


def test_claude_reasoning_returns_a_valid_recommendation(seeded_team):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tool = make_get_my_team_tool(conn, seeded_team)
        recommendation = ClaudeReasoning().reason("Who should I captain this week?", [tool])

    seeded_names = {"Mohamed Salah", "Erling Haaland", "Bukayo Saka"}
    assert recommendation.pick.player_name in seeded_names
    assert recommendation.alternative.player_name in seeded_names
    assert recommendation.alternative.player_id != recommendation.pick.player_id
    assert 0.0 <= recommendation.confidence <= 1.0
    assert recommendation.evidence
    assert recommendation.risks


def test_preference_changes_how_the_real_claude_recommendation_is_framed(
    seeded_team, tmp_path, monkeypatch
):
    """ADR-0010: Preferences colour every subsequent Recommendation."""
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))
    (tmp_path / "preferences.md").write_text(
        "- You must begin every single risk you list with the exact literal "
        "tag 'PREFTEST:' (all caps, followed by a colon and a space).\n"
    )

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tool = make_get_my_team_tool(conn, seeded_team)
        system = build_system_prompt(load_preferences())
        recommendation = ClaudeReasoning().reason(
            "Who should I captain this week?", [tool], system=system
        )

    assert recommendation.risks
    assert all(risk.startswith("PREFTEST:") for risk in recommendation.risks)


@pytest.fixture
def non_squad_player():
    """A pool player (issue 05) with Understat stats, not part of the seeded
    squad — real target for "is this player worth buying?"."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = %s", (NON_SQUAD_PLAYER_ID,))
        conn.execute(
            "INSERT INTO fpl_players "
            "(id, name, full_name, position, club, price, ownership, form, status, news, "
            " xg, xa) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                NON_SQUAD_PLAYER_ID,
                "Watkins",
                "Ollie Watkins",
                "FWD",
                "Aston Villa",
                9.0,
                12.5,
                6.8,
                "a",
                None,
                9.4,
                4.1,
            ),
        )
    yield NON_SQUAD_PLAYER_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = %s", (NON_SQUAD_PLAYER_ID,))


def test_claude_looks_up_and_cites_stats_for_a_player_outside_the_squad(
    seeded_team, non_squad_player
):
    """Issue 05's actual acceptance criterion: asking about a player not in
    the squad must make Claude call get_player/compare_players (widening
    submit_recommendation's id enum past the squad-only starting set via
    _record_known_players) and cite the returned xG/xA as evidence, rather
    than falling back to a squad pick."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_get_player_tool(conn),
            make_compare_players_tool(conn),
        ]
        recommendation = ClaudeReasoning().reason(
            "Is Ollie Watkins worth buying? Cite his underlying stats.", tools
        )

    assert recommendation.pick.player_id == non_squad_player
    assert recommendation.pick.player_name == "Watkins"
    assert any(
        "xg" in item.lower() or "xa" in item.lower() or "9.4" in item or "4.1" in item
        for item in recommendation.evidence
    )


def test_endpoint_repeated_calls_return_the_identical_pick_with_real_claude(seeded_team):
    with TestClient(app) as client:
        first = client.post("/recommendations", json={"question": "Who should I captain?"})
        second = client.post("/recommendations", json={"question": "Who should I captain?"})

    assert first.status_code == second.status_code == 200
    assert first.json()["pick"] == second.json()["pick"]
