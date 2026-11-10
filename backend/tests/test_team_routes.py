import os

import psycopg
from fastapi.testclient import TestClient

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.models import Alternative, Recommendation, RecommendationPick


def _set_gameweek(team_id: int, gameweek: int) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (team_id, gameweek),
        )


class _CountingSpyReasoning:
    """Returns a fixed Recommendation picking the squad's first player, and
    counts how many times reason() was actually called -- used to prove
    GET /team/suggestion only calls Claude once per gameweek, not once per
    request."""

    def __init__(self):
        self.calls = 0

    def reason(self, prompt, tools, system=""):
        self.calls += 1
        team = next(t for t in tools if t.name == "get_my_team").handler()
        player = team.players[0]
        return Recommendation(
            pick=RecommendationPick(player_id=player.id, player_name=player.name),
            confidence=0.7,
            evidence=["spy evidence"],
            risks=["spy risk"],
            alternative=Alternative(
                player_id=player.id, player_name=player.name, reason="spy alternative"
            ),
        )


def test_get_team_returns_the_squad(client, seeded_team):
    response = client.get("/team")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == seeded_team
    names = {p["name"] for p in body["players"]}
    assert names == {"Mohamed Salah", "Erling Haaland", "Bukayo Saka"}


def test_get_team_instincts_returns_this_gameweeks_notes(client, seeded_team):
    _set_gameweek(seeded_team, 4)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
            (seeded_team, 4, "thinking Rogers to Enzo"),
        )

    response = client.get("/team/instincts")

    assert response.status_code == 200
    assert [n["text"] for n in response.json()["notes"]] == ["thinking Rogers to Enzo"]


def test_get_team_suggestion_is_null_before_any_gameweek_is_known(client, seeded_team):
    response = client.get("/team/suggestion")

    assert response.status_code == 200
    assert response.json() is None


def test_get_team_suggestion_generates_and_caches(seeded_team):
    _set_gameweek(seeded_team, 5)
    spy = _CountingSpyReasoning()
    app.dependency_overrides[get_reasoning] = lambda: spy
    try:
        with TestClient(app) as test_client:
            first = test_client.get("/team/suggestion")
            second = test_client.get("/team/suggestion")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert spy.calls == 1  # second request served from the cache, not a fresh Claude call


def test_get_team_suggestion_regenerates_after_the_gameweek_advances(seeded_team):
    spy = _CountingSpyReasoning()
    app.dependency_overrides[get_reasoning] = lambda: spy
    try:
        with TestClient(app) as test_client:
            _set_gameweek(seeded_team, 5)
            test_client.get("/team/suggestion")
            _set_gameweek(seeded_team, 6)
            test_client.get("/team/suggestion")
    finally:
        app.dependency_overrides.clear()

    assert spy.calls == 2


def test_get_team_suggestion_logs_a_recommendation(seeded_team):
    _set_gameweek(seeded_team, 5)
    app.dependency_overrides[get_reasoning] = lambda: _CountingSpyReasoning()
    try:
        with TestClient(app) as test_client:
            test_client.get("/team/suggestion")
    finally:
        app.dependency_overrides.clear()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM recommendation_log WHERE team_id = %s AND gameweek = %s",
                (seeded_team, 5),
            )
            (count,) = cur.fetchone()

    assert count == 1
