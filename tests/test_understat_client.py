import httpx
import pytest

from fpl_coach.ingestion.understat_client import UnderstatClient

PLAYERS_RESPONSE = {
    "success": True,
    "players": [
        {
            "id": "8260",
            "player_name": "Erling Haaland",
            "team_title": "Manchester City",
            "xG": "28.795336209237576",
            "xA": "5.507687093690038",
        }
    ],
}


def _client_for(handler) -> UnderstatClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://understat.com", transport=transport)
    return UnderstatClient(http_client)


def test_get_league_players_posts_league_and_season_and_parses_players():
    seen_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request["method"] = request.method
        seen_request["path"] = request.url.path
        seen_request["body"] = request.content.decode()
        return httpx.Response(200, json=PLAYERS_RESPONSE)

    client = _client_for(handler)
    players = client.get_league_players("2025")

    assert seen_request["method"] == "POST"
    assert seen_request["path"] == "/main/getPlayersStats/"
    assert "league=EPL" in seen_request["body"]
    assert "season=2025" in seen_request["body"]

    assert players == PLAYERS_RESPONSE["players"]


def test_get_league_players_raises_when_understat_reports_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False})

    client = _client_for(handler)

    with pytest.raises(RuntimeError):
        client.get_league_players("2025")


def test_get_league_players_returns_empty_list_for_a_not_yet_started_season():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "players": []})

    client = _client_for(handler)

    assert client.get_league_players("2099") == []
