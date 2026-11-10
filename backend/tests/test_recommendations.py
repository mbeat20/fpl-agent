import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.models import (
    Alternative,
    PlanStep,
    Recommendation,
    RecommendationPick,
    TransferPlan,
)
from fpl_coach.preferences import load_preferences

NON_SQUAD_PLAYER_ID = 921


@pytest.fixture
def non_squad_player():
    """A pool player (issue 05) with Understat stats, not part of the seeded
    squad — exercises get_player being reachable for transfer-target
    questions, not just squad members."""
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


class _WorthBuyingSpyReasoning:
    """Verifies plumbing only: that get_player is reachable through
    /recommendations and a pick outside the seeded squad serializes
    correctly. Does NOT verify the issue-05 acceptance criterion itself —
    that Claude actually chooses to look up and cite stats for a
    non-squad player — which requires the real Reasoning implementation.
    See test_recommendations_integration.py for that."""

    def reason(self, prompt, tools, system=""):
        get_player = next(t for t in tools if t.name == "get_player")
        player = get_player.handler(name="Watkins")
        return Recommendation(
            pick=RecommendationPick(player_id=player.id, player_name=player.name),
            confidence=0.6,
            evidence=[f"{player.name} has posted {player.xg} xG and {player.xa} xA."],
            risks=["Sample evidence only."],
            alternative=Alternative(
                player_id=player.id, player_name=player.name, reason="spy alternative"
            ),
        )


class _InjuryNewsSpyReasoning:
    """Verifies plumbing only: that get_injury_news is reachable through
    /recommendations and its whole-squad output can populate a
    Recommendation's risks list. Does NOT verify the issue-07 acceptance
    criterion itself — that Claude actually chooses to call get_injury_news
    and summarize the whole squad for "what are the risks this week?" —
    which requires the real Reasoning implementation. See
    test_recommendations_integration.py for that."""

    def reason(self, prompt, tools, system=""):
        get_injury_news = next(t for t in tools if t.name == "get_injury_news")
        summary = get_injury_news.handler()
        first, second = summary.players[0], summary.players[1]
        return Recommendation(
            pick=RecommendationPick(player_id=first.player_id, player_name=first.player_name),
            confidence=0.5,
            evidence=["spy evidence"],
            risks=[
                f"{p.player_name}: injury_risk={p.injury_risk}, fixture_risk={p.fixture_risk}"
                for p in summary.players
            ],
            alternative=Alternative(
                player_id=second.player_id, player_name=second.player_name, reason="spy alternative"
            ),
        )


class _SpyReasoning:
    """Captures the system prompt it was called with, for plumbing assertions."""

    def __init__(self):
        self.last_system = None
        self.called = False

    def reason(self, prompt, tools, system=""):
        self.called = True
        self.last_system = system
        team = next(t for t in tools if t.name == "get_my_team").handler()
        player = team.players[0]
        return Recommendation(
            pick=RecommendationPick(player_id=player.id, player_name=player.name),
            confidence=0.5,
            evidence=["spy evidence"],
            risks=["spy risk"],
            alternative=Alternative(
                player_id=player.id, player_name=player.name, reason="spy alternative"
            ),
        )


class _PlanSpyReasoning:
    """Verifies plumbing only: that a TransferPlan (issue 12's longer-Horizon
    response type) round-trips through /recommendations and is logged.
    Does NOT verify the issue-12 acceptance criteria themselves -- that
    Claude actually infers a longer Horizon from the question and produces
    a plan grounded in real tool evidence -- which requires the real
    Reasoning implementation. See test_recommendations_integration.py."""

    def reason(self, prompt, tools, system=""):
        team = next(t for t in tools if t.name == "get_my_team").handler()
        first, second = team.players[0], team.players[1]
        return TransferPlan(
            horizon="multi-week",
            confidence=0.6,
            # Deliberately out of gameweek order: nothing in the submit_plan
            # schema or its description requires Claude to emit steps
            # ascending, so the nearest-step logging in routes.py must pick
            # the step with the smallest gameweek rather than trusting
            # steps[0] -- see test_ask_logs_only_the_plans_nearest_step.
            steps=[
                PlanStep(
                    gameweek=2,
                    pick=RecommendationPick(player_id=second.id, player_name=second.name),
                    evidence=["spy evidence gw2"],
                    risks=["spy risk gw2"],
                ),
                PlanStep(
                    gameweek=1,
                    pick=RecommendationPick(player_id=first.id, player_name=first.name),
                    evidence=["spy evidence gw1"],
                    risks=["spy risk gw1"],
                ),
            ],
        )


def test_ask_returns_a_transfer_plan_shape(seeded_team):
    app.dependency_overrides[get_reasoning] = lambda: _PlanSpyReasoning()
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/recommendations",
                json={"question": "What should my long-term transfer plan be?"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"horizon", "confidence", "steps"}
    assert len(body["steps"]) == 2
    assert {step["gameweek"] for step in body["steps"]} == {1, 2}
    assert set(body["steps"][0]) == {"gameweek", "pick", "evidence", "risks"}


def test_ask_logs_only_the_plans_nearest_step(seeded_team):
    """Issue 12: only the nearest step is an actual commitment right now --
    logging every future-dated step would leave the rest as permanently
    unresolved speculation in recommendation_log (ADR-0012). The logged row
    must carry the step's OWN gameweek (1, from _PlanSpyReasoning), not
    current_gameweek's -- seeded deliberately different (9) here so a
    regression that silently substitutes current_gameweek back in (as
    log_recommendation's default does) fails this assertion instead of
    passing by coincidence. _PlanSpyReasoning's steps are also deliberately
    out of gameweek order, so this doubles as the regression test for
    routes.py picking the step with the smallest gameweek rather than
    trusting steps[0] -- nothing in the submit_plan schema or its
    description requires Claude to emit steps ascending, so this is not an
    assumption routes.py should get to make."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 9),
        )

    app.dependency_overrides[get_reasoning] = lambda: _PlanSpyReasoning()
    try:
        with TestClient(app) as test_client:
            test_client.post(
                "/recommendations",
                json={"question": "What should my long-term transfer plan be?"},
            )
    finally:
        app.dependency_overrides.clear()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek, player_id, player_name FROM recommendation_log WHERE team_id = %s",
                (seeded_team,),
            )
            rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0]["player_name"] == "Mohamed Salah"
    assert rows[0]["gameweek"] == 1


def test_ask_passes_the_preferences_file_as_the_system_prompt(
    seeded_team, tmp_path, monkeypatch
):
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))
    (tmp_path / "preferences.md").write_text("- Prefers set-and-forget captains.\n")

    spy = _SpyReasoning()
    app.dependency_overrides[get_reasoning] = lambda: spy
    try:
        with TestClient(app) as test_client:
            test_client.post("/recommendations", json={"question": "Who should I captain?"})
    finally:
        app.dependency_overrides.clear()

    assert spy.called
    assert "Prefers set-and-forget captains." in spy.last_system


def test_remember_instruction_appends_to_preferences_and_skips_reasoning(
    client, seeded_team, tmp_path, monkeypatch
):
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))

    response = client.post(
        "/recommendations",
        json={"question": "remember: I dislike knee-jerk transfers"},
    )

    assert response.status_code == 200
    assert response.json() == {"remembered": "I dislike knee-jerk transfers"}
    assert "I dislike knee-jerk transfers" in load_preferences()


def test_remember_thinking_instruction_logs_an_instinct_not_a_preference(
    client, seeded_team, tmp_path, monkeypatch
):
    """Issue 13: same "remember: X" phrasing as a Preference, but "thinking
    ..." text routes to the Instinct table instead of preferences.md."""
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 7),
        )

    response = client.post(
        "/recommendations",
        json={"question": "remember: thinking Rogers to Enzo"},
    )

    assert response.status_code == 200
    assert response.json() == {"remembered": "thinking Rogers to Enzo"}
    assert "Rogers to Enzo" not in load_preferences()

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT text, gameweek FROM instincts WHERE team_id = %s", (seeded_team,)
            )
            row = cur.fetchone()
    assert row["text"] == "thinking Rogers to Enzo"
    assert row["gameweek"] == 7


def test_one_off_question_is_not_persisted_to_preferences(
    client, seeded_team, tmp_path, monkeypatch
):
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))

    client.post("/recommendations", json={"question": "give me the safest pick this week"})

    assert "safest pick this week" not in load_preferences()


def test_ask_returns_a_recommendation_shape(client, seeded_team):
    response = client.post("/recommendations", json={"question": "Who should I captain?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"pick", "confidence", "evidence", "risks", "alternative"}
    assert set(body["pick"]) == {"player_id", "player_name"}
    assert set(body["alternative"]) == {"player_id", "player_name", "reason"}


def test_pick_comes_from_the_seeded_squad(client, seeded_team):
    response = client.post("/recommendations", json={"question": "Who should I captain?"})

    body = response.json()
    assert body["pick"]["player_name"] == "Erling Haaland"  # highest-priced seeded player


def test_ask_logs_the_recommendation_for_later_outcome_matching(client, seeded_team):
    """Issue 10: enough identity (player, gameweek) to later match this
    Recommendation against what actually happened."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 6),
        )

    response = client.post("/recommendations", json={"question": "Who should I captain?"})
    picked = response.json()["pick"]

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek, player_id, player_name FROM recommendation_log "
                "WHERE team_id = %s",
                (seeded_team,),
            )
            row = cur.fetchone()

    assert row["gameweek"] == 6
    assert row["player_id"] == picked["player_id"]
    assert row["player_name"] == picked["player_name"]


def test_repeated_calls_return_the_identical_primary_pick(client, seeded_team):
    first = client.post("/recommendations", json={"question": "Who should I captain?"}).json()
    second = client.post("/recommendations", json={"question": "Who should I captain?"}).json()

    assert first["pick"] == second["pick"]


def test_injury_news_wiring_reaches_the_whole_squad(seeded_team):
    app.dependency_overrides[get_reasoning] = lambda: _InjuryNewsSpyReasoning()
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/recommendations", json={"question": "What are the risks this week?"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    seeded_names = {"Mohamed Salah", "Erling Haaland", "Bukayo Saka"}
    mentioned = {name for risk in body["risks"] for name in seeded_names if name in risk}
    assert mentioned == seeded_names


def test_worth_buying_wiring_reaches_get_player_for_a_non_squad_player(
    seeded_team, non_squad_player
):
    """Confirms get_player is wired into /recommendations and reachable for
    a player outside the squad. Not a test of issue 05's acceptance
    criterion itself (see module docstring on _WorthBuyingSpyReasoning)."""
    app.dependency_overrides[get_reasoning] = lambda: _WorthBuyingSpyReasoning()
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/recommendations", json={"question": "Is Ollie Watkins worth buying?"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["pick"]["player_id"] == non_squad_player
    assert body["pick"]["player_name"] == "Watkins"
    assert any("xG" in item and "xA" in item for item in body["evidence"])
