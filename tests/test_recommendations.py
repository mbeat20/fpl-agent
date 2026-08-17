import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.models import Alternative, Recommendation, RecommendationPick
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
