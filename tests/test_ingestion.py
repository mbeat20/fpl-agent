import os

import httpx
import psycopg
from psycopg.rows import dict_row

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.pipeline import (
    _resolve_understat_players,
    _understat_season_from_bootstrap,
    run_ingestion,
)
from fpl_coach.ingestion.understat_client import UnderstatClient
from fpl_coach.tools import get_my_team

TEST_TEAM_ID = 9001
FPL_ENTRY_ID = 555
TEST_ELEMENT_IDS = (101, 102, 103)

BOOTSTRAP = {
    "elements": [
        {
            "id": 101,
            "web_name": "Salah",
            "first_name": "Mohamed",
            "second_name": "Salah",
            "element_type": 3,
            "team": 1,
            "now_cost": 135,
            "selected_by_percent": "45.2",
            "form": "6.5",
            "status": "a",
            "news": "",
            "transfers_in_event": 5000,
            "transfers_out_event": 1000,
            "cost_change_event": 0,
            "event_points": 12,
        },
        {
            "id": 102,
            "web_name": "Haaland",
            "first_name": "Erling",
            "second_name": "Haaland",
            "element_type": 4,
            "team": 2,
            "now_cost": 150,
            "selected_by_percent": "60.1",
            "form": "8.2",
            "status": "d",
            "news": "Knock - 75% chance of playing",
            "transfers_in_event": 500,
            "transfers_out_event": 8000,
            "cost_change_event": -1,
            "event_points": 2,
        },
        {
            "id": 103,
            "web_name": "Saka",
            "first_name": "Bukayo",
            "second_name": "Saka",
            "element_type": 3,
            "team": 3,
            "now_cost": 100,
            "selected_by_percent": "30.0",
            "form": "5.1",
            "status": "a",
            "news": "",
            "transfers_in_event": 0,
            "transfers_out_event": 0,
            "cost_change_event": 0,
            "event_points": 6,
        },
    ],
    "teams": [
        {"id": 1, "short_name": "LIV"},
        {"id": 2, "short_name": "MCI"},
        {"id": 3, "short_name": "ARS"},
    ],
    "events": [
        {"id": 1, "is_current": False, "is_next": False, "deadline_time": "2025-08-16T14:00:00Z"},
        {"id": 2, "is_current": True, "is_next": False, "deadline_time": "2025-08-23T14:00:00Z"},
        {"id": 3, "is_current": False, "is_next": True, "deadline_time": "2025-08-30T14:00:00Z"},
    ],
}

FIXTURES = [
    {
        "id": 1,
        "event": 2,
        "team_h": 1,
        "team_a": 2,
        "team_h_difficulty": 4,
        "team_a_difficulty": 2,
        "kickoff_time": "2026-08-15T14:00:00Z",
    },
    {
        "id": 2,
        "event": 2,
        "team_h": 3,
        "team_a": 1,
        "team_h_difficulty": 3,
        "team_a_difficulty": 3,
        "kickoff_time": "2026-08-22T14:00:00Z",
    },
]

ENTRY = {"id": FPL_ENTRY_ID, "name": "Ingested Team"}

ENTRY_PICKS = {
    "picks": [
        {"element": 101, "position": 1, "multiplier": 1},
        {"element": 102, "position": 2, "multiplier": 2},
        {"element": 103, "position": 3, "multiplier": 1},
    ]
}

_RESPONSES_BY_PATH = {
    "/api/bootstrap-static/": BOOTSTRAP,
    "/api/fixtures/": FIXTURES,
    f"/api/entry/{FPL_ENTRY_ID}/": ENTRY,
    f"/api/entry/{FPL_ENTRY_ID}/event/2/picks/": ENTRY_PICKS,
}

# Deliberately omits "Bukayo Saka" so his pool row exercises the
# unmatched-stays-NULL path.
UNDERSTAT_PLAYERS = [
    {"id": "1", "player_name": "Mohamed Salah", "team_title": "Liverpool", "xG": "8.8", "xA": "6.4"},
    {"id": "2", "player_name": "Erling Haaland", "team_title": "Manchester City", "xG": "28.8", "xA": "5.5"},
]


def _fake_client() -> FPLClient:
    """Fakes the FPL API at its HTTP boundary (httpx.MockTransport with
    canned JSON), not by mocking FPLClient's methods."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_RESPONSES_BY_PATH[request.url.path])

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://fantasy.premierleague.com/api", transport=transport
    )
    return FPLClient(http_client)


def _fake_understat_client(players: list[dict] = UNDERSTAT_PLAYERS) -> UnderstatClient:
    """Fakes Understat at its HTTP boundary, same convention as the FPL client."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "players": players})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://understat.com", transport=transport)
    return UnderstatClient(http_client)


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        cur.execute("DELETE FROM fixtures")
        cur.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_ELEMENT_IDS),))
    conn.commit()


def test_run_ingestion_writes_correctly_shaped_rows():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = run_ingestion(
            conn,
            _fake_client(),
            _fake_understat_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
            understat_season="2025",
        )

        assert summary.team_name == "Ingested Team"
        assert summary.players_ingested == 3
        assert summary.fixtures_ingested == 2
        assert summary.players_pool_ingested == 3
        assert summary.understat_season == "2025"
        assert summary.understat_matched == 2
        assert summary.outcomes_recorded == 0

        with conn.cursor() as cur:
            cur.execute("SELECT name FROM teams WHERE id = %s", (TEST_TEAM_ID,))
            assert cur.fetchone()["name"] == "Ingested Team"

            cur.execute(
                "SELECT name, position, club, price, ownership, form, status, news, "
                "fpl_element_id "
                "FROM players WHERE team_id = %s ORDER BY name",
                (TEST_TEAM_ID,),
            )
            players = {row["name"]: row for row in cur.fetchall()}

        assert set(players) == {"Salah", "Haaland", "Saka"}
        haaland = players["Haaland"]
        assert haaland["position"] == "FWD"
        assert haaland["club"] == "MCI"
        assert float(haaland["price"]) == 15.0
        assert float(haaland["ownership"]) == 60.1
        assert float(haaland["form"]) == 8.2
        assert haaland["status"] == "d"
        assert haaland["news"] == "Knock - 75% chance of playing"
        assert haaland["fpl_element_id"] == 102

        with conn.cursor() as cur:
            cur.execute("SELECT gameweek, home_team, away_team FROM fixtures ORDER BY id")
            fixture_rows = cur.fetchall()
        assert fixture_rows[0]["home_team"] == "LIV"
        assert fixture_rows[0]["away_team"] == "MCI"
        assert fixture_rows[1]["gameweek"] == 2

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, full_name, xg, xa, transfers_in_event, transfers_out_event, "
                "cost_change_event FROM fpl_players WHERE id = ANY(%s) ORDER BY id",
                (list(TEST_ELEMENT_IDS),),
            )
            pool = {row["name"]: row for row in cur.fetchall()}

        assert set(pool) == {"Salah", "Haaland", "Saka"}
        assert pool["Salah"]["full_name"] == "Mohamed Salah"
        assert float(pool["Salah"]["xg"]) == 8.8
        assert float(pool["Salah"]["xa"]) == 6.4
        assert float(pool["Haaland"]["xg"]) == 28.8
        assert pool["Saka"]["xg"] is None
        assert pool["Saka"]["xa"] is None

        assert pool["Salah"]["transfers_in_event"] == 5000
        assert pool["Salah"]["transfers_out_event"] == 1000
        assert pool["Haaland"]["cost_change_event"] == -1

        team = get_my_team(conn, TEST_TEAM_ID)
        assert team.name == "Ingested Team"
        assert {p.name for p in team.players} == {"Salah", "Haaland", "Saka"}
        assert {p.id for p in team.players} == set(TEST_ELEMENT_IDS)
    finally:
        _cleanup(conn)
        conn.close()


def test_run_ingestion_replaces_prior_seed_data():
    """After a refresh, get_my_team must reflect the real ingested data, not
    whatever (e.g. ticket-01 seed) data previously lived at that team id."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Seed Team")
            )
            cur.execute(
                "INSERT INTO players (team_id, name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s)",
                (TEST_TEAM_ID, "Seed Player", "MID", "SEED", 5.0),
            )
        conn.commit()

        run_ingestion(
            conn,
            _fake_client(),
            _fake_understat_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
            understat_season="2025",
        )

        team = get_my_team(conn, TEST_TEAM_ID)
        assert team.name == "Ingested Team"
        names = {p.name for p in team.players}
        assert names == {"Salah", "Haaland", "Saka"}
        assert "Seed Player" not in names
    finally:
        _cleanup(conn)
        conn.close()


def test_run_ingestion_preserves_a_same_gameweek_instinct():
    """Issue 10 regression: run_ingestion used to DELETE FROM teams then
    re-INSERT it, which cascade-deleted gameweek_state/instincts (FK'd to
    teams(id) ON DELETE CASCADE) on every single cycle -- silently wiping
    even a same-gameweek Instinct the purge below was never meant to touch.
    Fixed by upserting the team row instead. BOOTSTRAP resolves gameweek 2."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Seed Team"))
            cur.execute(
                "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)", (TEST_TEAM_ID, 2)
            )
            cur.execute(
                "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
                (TEST_TEAM_ID, 2, "thinking test"),
            )
        conn.commit()

        run_ingestion(
            conn,
            _fake_client(),
            _fake_understat_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
            understat_season="2025",
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM instincts WHERE team_id = %s", (TEST_TEAM_ID,))
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(conn)
        conn.close()


def test_run_ingestion_purges_a_stale_gameweek_instinct():
    """The purge (`DELETE FROM instincts WHERE gameweek <> current`) only
    means something once same-gameweek rows survive the team refresh --
    ticket 13's AC4 test gap that let the cascade bug above hide."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Seed Team"))
            cur.execute(
                "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)", (TEST_TEAM_ID, 1)
            )
            cur.execute(
                "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
                (TEST_TEAM_ID, 1, "thinking stale"),
            )
        conn.commit()

        run_ingestion(
            conn,
            _fake_client(),
            _fake_understat_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
            understat_season="2025",
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM instincts WHERE team_id = %s", (TEST_TEAM_ID,))
            assert cur.fetchone()["n"] == 0
    finally:
        _cleanup(conn)
        conn.close()


def test_run_ingestion_records_outcomes_for_recommendations_from_an_earlier_gameweek():
    """Issue 10: a subsequent ingestion cycle (this one, gameweek 2) fills
    in the outcome of a Recommendation logged against an earlier gameweek,
    from bootstrap-static's own event_points/cost_change_event."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Seed Team")
            )
            cur.execute(
                "INSERT INTO recommendation_log (team_id, gameweek, player_id, player_name) "
                "VALUES (%s, %s, %s, %s)",
                (TEST_TEAM_ID, 1, 102, "Erling Haaland"),
            )
        conn.commit()

        summary = run_ingestion(
            conn,
            _fake_client(),
            _fake_understat_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
            understat_season="2025",
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_price_change, outcome_points, outcome_recorded_at "
                "FROM recommendation_log WHERE team_id = %s AND player_id = %s",
                (TEST_TEAM_ID, 102),
            )
            row = cur.fetchone()

        assert summary.outcomes_recorded == 1
        assert float(row["outcome_price_change"]) == -0.1  # cost_change_event -1 -> tenths
        assert row["outcome_points"] == 2
        assert row["outcome_recorded_at"] is not None
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM recommendation_log WHERE team_id = %s", (TEST_TEAM_ID,))
        conn.commit()
        _cleanup(conn)
        conn.close()


def test_understat_season_derived_from_gameweek_1_deadline():
    assert _understat_season_from_bootstrap(BOOTSTRAP["events"]) == "2025"


def test_resolve_understat_players_falls_back_a_year_when_season_is_empty():
    calls = []

    class _StubUnderstatClient:
        def get_league_players(self, season, league="EPL"):
            calls.append(season)
            return [] if season == "2026" else UNDERSTAT_PLAYERS

    season, players = _resolve_understat_players(_StubUnderstatClient(), "2026")

    assert calls == ["2026", "2025"]
    assert season == "2025"
    assert players == UNDERSTAT_PLAYERS
