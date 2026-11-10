import httpx

DEFAULT_BASE_URL = "https://understat.com"


class UnderstatClient:
    """Thin wrapper over Understat's internal stats endpoint.

    Understat has no public API; the site's own frontend JS (league.min.js)
    POSTs to this same endpoint to render the league players table, so this
    mirrors that request rather than scraping/parsing HTML. Takes an
    httpx.Client so tests can fake it at its HTTP boundary (httpx.MockTransport
    with canned JSON) rather than mocking this class.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(base_url=DEFAULT_BASE_URL, timeout=10.0)

    def get_league_players(self, season: str, league: str = "EPL") -> list[dict]:
        """Whole-season aggregated per-player stats (id, player_name, xG, xA,
        team_title, ...; all numeric fields as strings). Empty list if the
        given season has no data yet (e.g. before a new season kicks off)."""
        response = self._client.post(
            "/main/getPlayersStats/", data={"league": league, "season": season}
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError("Understat getPlayersStats did not return success")
        return payload["players"]
