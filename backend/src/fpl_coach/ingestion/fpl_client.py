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
        """The user's actual 15-player squad for the given gameweek.

        Issue 20: the whole response matters now, not just `picks`.
        `entry_history` carries bank, squad value and transfers; each pick
        carries its XI/bench `position`, `multiplier` and the armband flags.

        It does NOT carry selling_price or purchase_price. Those are on
        /my-team/{id}/, which answers 403 without an authenticated session --
        verified live 2026-08-28. See sellable_value() for what can honestly
        be derived without it."""
        return self._get(f"/entry/{entry_id}/event/{gameweek}/picks/")

    def get_entry_history(self, entry_id: int) -> dict:
        """Issue 20: per-gameweek points/rank (`current`), previous seasons
        (`past`), and the chips already played (`chips`)."""
        return self._get(f"/entry/{entry_id}/history/")

    def get_element_summary(self, element_id: int) -> dict:
        """Issue 20: one player's per-fixture history, including minutes.

        One HTTP call per player -- never fan this out across the whole pool.
        run_ingestion scopes it to the squad, and get_player fetches on demand
        for anyone else the user asks about."""
        return self._get(f"/element-summary/{element_id}/")

    def _get(self, path: str):
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()
