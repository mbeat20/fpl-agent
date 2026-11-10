import httpx

DEFAULT_BASE_URL = "https://fantasy.premierleague.com/api"


class FPLClient:
    """Thin wrapper over the official FPL API.

    Takes an httpx.Client so tests can fake the FPL API at its HTTP boundary
    (httpx.MockTransport with canned JSON) rather than mocking this class.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(base_url=DEFAULT_BASE_URL, timeout=10.0)

    def get_bootstrap(self) -> dict:
        """Players, clubs, and gameweeks — https://.../api/bootstrap-static/"""
        return self._get("/bootstrap-static/")

    def get_fixtures(self) -> list[dict]:
        return self._get("/fixtures/")

    def get_entry(self, entry_id: int) -> dict:
        """The user's FPL team (entry) itself — name, etc."""
        return self._get(f"/entry/{entry_id}/")

    def get_entry_picks(self, entry_id: int, gameweek: int) -> dict:
        """The user's actual 15-player squad for the given gameweek."""
        return self._get(f"/entry/{entry_id}/event/{gameweek}/picks/")

    def _get(self, path: str):
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()
