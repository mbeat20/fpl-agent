import os

import httpx
import psycopg
from psycopg.rows import dict_row

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.pipeline import (
    run_ingestion,
)
from fpl_coach.tools import get_my_team
from fpl_coach.tools.get_player import get_player

TEST_TEAM_ID = 9001
FPL_ENTRY_ID = 555
TEST_ELEMENT_IDS = (101, 102, 103)
# Issue 19: derived from BOOTSTRAP rather than written out, so a new event in
# the fixture can never be left behind in the shared database's gameweeks
# table by a cleanup that has drifted out of date.

BOOTSTRAP = {
    "elements": [
        {
            "id": 101,
            "web_name": "Salah",
            "first_name": "Mohamed",
            "second_name": "Salah",
            "element_type": 3,
            "team": 9001,
            "now_cost": 135,
            "selected_by_percent": "45.2",
            "form": "6.5",
            "status": "a",
            "news": "",
            "transfers_in_event": 5000,
            "transfers_out_event": 1000,
            "cost_change_event": 0,
            "event_points": 12,
            "minutes": 180,
            "starts": 2,
            "starts_per_90": 1.0,
            "expected_goals": "8.80",
            "expected_assists": "6.40",
        },
        {
            "id": 102,
            "web_name": "Haaland",
            "first_name": "Erling",
            "second_name": "Haaland",
            "element_type": 4,
            "team": 9002,
            "now_cost": 150,
            "selected_by_percent": "60.1",
            "form": "8.2",
            "status": "d",
            "news": "Knock - 75% chance of playing",
            "transfers_in_event": 500,
            "transfers_out_event": 8000,
            "cost_change_event": -1,
            "event_points": 2,
            "minutes": 180,
            "starts": 2,
            "starts_per_90": 1.0,
            "expected_goals": "28.80",
            "expected_assists": "5.50",
        },
        {
            "id": 103,
            "web_name": "Saka",
            "first_name": "Bukayo",
            "second_name": "Saka",
            "element_type": 3,
            "team": 9003,
            "now_cost": 100,
            "selected_by_percent": "30.0",
            "form": "5.1",
            "status": "a",
            "news": "",
            "transfers_in_event": 0,
            "transfers_out_event": 0,
            "cost_change_event": 0,
            "event_points": 6,
            # Issue 20: a fit player barely playing -- the rotation risk that
            # `status` alone can never expose.
            "minutes": 100,
            "starts": 0,
            "starts_per_90": 0.0,
            # Deliberately no expected_goals/expected_assists: an absent
            # field must stay NULL (unknown), never be scored as a zero.
        },
    ],
    # Issue 21: ids deliberately outside the real 1-20 range. `clubs` is a
    # global table in a shared database, so a fake bootstrap using real club
    # ids would upsert a WRONG id -> short-name mapping over the real one --
    # and a wrong opponent name written into player_gameweek_history is worse
    # than the NULL it replaced. Short names are fake for the same reason.
    "teams": [
        {"id": 9001, "name": "Zed Liver", "short_name": "ZLV"},
        {"id": 9002, "name": "Zed City", "short_name": "ZCI"},
        {"id": 9003, "name": "Zed Arsenal", "short_name": "ZAR"},
    ],
    "events": [
        {"id": 1, "is_current": False, "is_next": False, "finished": True,
         "deadline_time": "2025-08-16T14:00:00Z"},
        {"id": 2, "is_current": True, "is_next": False, "deadline_time": "2025-08-23T14:00:00Z"},
        {"id": 3, "is_current": False, "is_next": True, "deadline_time": "2025-08-30T14:00:00Z"},
    ],
    # Issue 20: FPL issues a fresh set of chips for each half of the season,
    # which is why chip_status keys on the window rather than the name.
    "chips": [
        {"name": "wildcard", "chip_type": "transfer", "start_event": 1, "stop_event": 19},
        {"name": "wildcard", "chip_type": "transfer", "start_event": 20, "stop_event": 38},
        {"name": "bboost", "chip_type": "team", "start_event": 1, "stop_event": 19},
    ],
}

FIXTURES = [
    {
        "id": 1,
        "event": 2,
        "team_h": 9001,
        "team_a": 9002,
        "team_h_difficulty": 4,
        "team_a_difficulty": 2,
        "kickoff_time": "2026-08-15T14:00:00Z",
    },
    {
        "id": 2,
        "event": 2,
        "team_h": 9003,
        "team_a": 9001,
        "team_h_difficulty": 3,
        "team_a_difficulty": 3,
        "kickoff_time": "2026-08-22T14:00:00Z",
    },
]

ENTRY = {"id": FPL_ENTRY_ID, "name": "Ingested Team"}

TEST_GAMEWEEK_IDS = tuple(event["id"] for event in BOOTSTRAP["events"])
TEST_CLUB_IDS = tuple(club["id"] for club in BOOTSTRAP["teams"])

ENTRY_PICKS = {
    "active_chip": None,
    "entry_history": {
        "event": 2,
        "points": 61,
        "total_points": 118,
        "overall_rank": 250_000,
        # Issue 20: FPL's `value` is team value -- squad PLUS bank. 1000 with
        # a bank of 5 means a 99.5m squad, not a 100.0m one.
        "bank": 5,
        "value": 1000,
        "event_transfers": 1,
        "event_transfers_cost": 4,
        "points_on_bench": 3,
    },
    "picks": [
        {"element": 101, "position": 1, "multiplier": 2, "is_captain": True,
         "is_vice_captain": False},
        {"element": 102, "position": 2, "multiplier": 1, "is_captain": False,
         "is_vice_captain": True},
        {"element": 103, "position": 12, "multiplier": 0, "is_captain": False,
         "is_vice_captain": False},
    ],
}

ENTRY_HISTORY = {
    "current": [{"event": 1, "points": 57, "overall_rank": 300_000}],
    "past": [],
    "chips": [{"name": "wildcard", "time": "2025-08-20T10:00:00Z", "event": 1}],
}

# Issue 24: two rounds, each carrying `value` (FPL's price in tenths).
# Outcome resolution reads points from the row's OWN gameweek and price change
# as the next recorded price minus that one, so a single round would leave the
# price unresolvable and the mechanism untested.
ELEMENT_SUMMARY = {
    "fixtures": [],
    "history_past": [],
    "history": [
        {"round": 1, "minutes": 90, "starts": 1, "total_points": 12,
         "was_home": True, "opponent_team": 2, "value": 95},
        {"round": 2, "minutes": 90, "starts": 1, "total_points": 2,
         "was_home": False, "opponent_team": 3, "value": 94},
    ],
}

_RESPONSES_BY_PATH = {
    "/api/bootstrap-static/": BOOTSTRAP,
    "/api/fixtures/": FIXTURES,
    f"/api/entry/{FPL_ENTRY_ID}/": ENTRY,
    f"/api/entry/{FPL_ENTRY_ID}/event/2/picks/": ENTRY_PICKS,
    f"/api/entry/{FPL_ENTRY_ID}/history/": ENTRY_HISTORY,
}


def _fake_client() -> FPLClient:
    """Fakes the FPL API at its HTTP boundary (httpx.MockTransport with
    canned JSON), not by mocking FPLClient's methods."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Issue 20: element-summary is one path per player, so it cannot be a
        # dict key. Counted so a test can assert the fan-out never happens.
        if path.startswith("/api/element-summary/"):
            element_summary_calls.append(path)
            return httpx.Response(200, json=ELEMENT_SUMMARY)
        return httpx.Response(200, json=_RESPONSES_BY_PATH[path])

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://fantasy.premierleague.com/api", transport=transport
    )
    return FPLClient(http_client)


element_summary_calls: list[str] = []


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        cur.execute("DELETE FROM fixtures")
        cur.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TEST_ELEMENT_IDS),))
        cur.execute(
            "DELETE FROM player_gameweek_history WHERE fpl_player_id = ANY(%s)",
            (list(TEST_ELEMENT_IDS),),
        )
        # Issue 19: the gameweek calendar is global -- there is one football
        # season, not one per team -- so a fake ingestion overwrites the real
        # deadlines for whichever gameweek ids its BOOTSTRAP names. Removing
        # them leaves the calendar short rather than wrong, and `uv run
        # fpl-ingest` restores it, same standing rule as `fixtures` (which
        # this module also wipes unscoped, and for the same reason).
        cur.execute("DELETE FROM gameweeks WHERE id = ANY(%s)", (list(TEST_GAMEWEEK_IDS),))
        # Issue 21: same reasoning as gameweeks above -- `clubs` is global.
        cur.execute("DELETE FROM clubs WHERE id = ANY(%s)", (list(TEST_CLUB_IDS),))
    conn.commit()
    element_summary_calls.clear()


def test_run_ingestion_writes_correctly_shaped_rows():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = run_ingestion(
            conn,
            _fake_client(),
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
        )

        assert summary.team_name == "Ingested Team"
        assert summary.players_ingested == 3
        assert summary.fixtures_ingested == 2
        assert summary.players_pool_ingested == 3
        assert summary.players_with_xg == 2
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
        assert haaland["club"] == "ZCI"
        assert float(haaland["price"]) == 15.0
        assert float(haaland["ownership"]) == 60.1
        assert float(haaland["form"]) == 8.2
        assert haaland["status"] == "d"
        assert haaland["news"] == "Knock - 75% chance of playing"
        assert haaland["fpl_element_id"] == 102

        with conn.cursor() as cur:
            cur.execute("SELECT gameweek, home_team, away_team FROM fixtures ORDER BY id")
            fixture_rows = cur.fetchall()
        assert fixture_rows[0]["home_team"] == "ZLV"
        assert fixture_rows[0]["away_team"] == "ZCI"
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
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
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
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
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
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
        )

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM instincts WHERE team_id = %s", (TEST_TEAM_ID,))
            assert cur.fetchone()["n"] == 0
    finally:
        _cleanup(conn)
        conn.close()


def test_run_ingestion_records_outcomes_for_recommendations_from_an_earlier_gameweek():
    """Issue 10, rewritten by issue 24: a later ingestion cycle fills in the
    outcome of a Recommendation logged against an earlier gameweek.

    The numbers now come from that gameweek's own row in
    player_gameweek_history, not from bootstrap-static's `event_points` /
    `cost_change_event` -- those describe only the CURRENT event, which is
    exactly why the old implementation could not resolve anything older than
    `gameweek - 1` without writing the wrong gameweek's data onto it."""
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
            TEST_TEAM_ID,
            FPL_ENTRY_ID,
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome_price_change, outcome_points, outcome_recorded_at "
                "FROM recommendation_log WHERE team_id = %s AND player_id = %s",
                (TEST_TEAM_ID, 102),
            )
            row = cur.fetchone()

        assert summary.outcomes_recorded == 1
        # GW1's own points (12), not GW2's (2) -- the whole point of issue 24.
        assert row["outcome_points"] == 12
        # And GW1's own price movement: 9.4 - 9.5, from the two `value` readings.
        assert float(row["outcome_price_change"]) == -0.1
        assert row["outcome_recorded_at"] is not None
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM recommendation_log WHERE team_id = %s", (TEST_TEAM_ID,))
        conn.commit()
        _cleanup(conn)
        conn.close()


def test_the_gameweek_calendar_is_ingested_with_real_deadlines():
    """Issue 19: content is placed in the gameweek it was published FOR, and
    the boundary that decides that is the deadline. Until this ran, the only
    time the database knew about a gameweek was fixtures.kickoff_time -- when
    the football starts, not when the manager loses the ability to act."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = run_ingestion(
            conn, _fake_client(), TEST_TEAM_ID, FPL_ENTRY_ID
        )
        assert summary.gameweeks_of_calendar == len(BOOTSTRAP["events"])

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, deadline_time, is_current, is_next FROM gameweeks "
                "WHERE id = ANY(%s) ORDER BY id",
                (list(TEST_GAMEWEEK_IDS),),
            )
            rows = cur.fetchall()

        assert [row["id"] for row in rows] == list(TEST_GAMEWEEK_IDS)
        assert rows[1]["is_current"] is True
        assert rows[2]["is_next"] is True
        # Deadlines, not kickoffs -- GW2's deadline precedes its first
        # fixture, and confusing the two puts deadline-eve opinion in the
        # wrong gameweek.
        assert rows[1]["deadline_time"].isoformat().startswith("2025-08-23T14:00")

        # Idempotent: a second run must upsert rather than duplicate or wipe,
        # which is issue 10's cascade lesson applied to a global table.
        run_ingestion(conn, _fake_client(), TEST_TEAM_ID, FPL_ENTRY_ID)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM gameweeks WHERE id = ANY(%s)",
                (list(TEST_GAMEWEEK_IDS),),
            )
            assert cur.fetchone()["n"] == len(TEST_GAMEWEEK_IDS)
    finally:
        _cleanup(conn)
        conn.close()


def test_the_club_identity_map_is_ingested():
    """Issue 21: every other table stores a club as its short name, which
    works right up until something outside ingestion meets a bare FPL team id
    and has nowhere to look it up -- which is why issue 20's on-demand minutes
    path was storing a NULL opponent."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = run_ingestion(
            conn, _fake_client(), TEST_TEAM_ID, FPL_ENTRY_ID
        )
        assert summary.clubs_ingested == len(BOOTSTRAP["teams"])

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, short_name FROM clubs WHERE id = ANY(%s) ORDER BY id",
                ([club["id"] for club in BOOTSTRAP["teams"]],),
            )
            rows = cur.fetchall()

        assert [row["short_name"] for row in rows] == [
            club["short_name"] for club in BOOTSTRAP["teams"]
        ]
        # The short name is what fixtures and players already store, so it has
        # to round-trip exactly -- a mismatch here silently empties every
        # fixture-run query.
        assert rows[0]["short_name"] == BOOTSTRAP["teams"][0]["short_name"]
    finally:
        _cleanup(conn)
        conn.close()


# --- issue 20: the state that used to be fetched and thrown away -------------


def _ingest_once(conn):
    return run_ingestion(
        conn,
        _fake_client(),
        TEST_TEAM_ID,
        FPL_ENTRY_ID,
    )


def test_entry_money_and_transfers_are_ingested():
    """These were all in the picks payload run_ingestion already fetched, and
    all discarded -- which is why fpl_rules.py could instruct the model to
    check affordability against a schema that had no bank column."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        team = get_my_team(conn, TEST_TEAM_ID)

        assert team.finances.bank == 0.5
        assert team.finances.team_value == 100.0
        # squad = team value - bank, never team value itself
        assert team.finances.squad_selling_value == 99.5
        assert team.event_transfers == 1
        assert team.event_transfers_cost == 4
        assert team.overall_rank == 250_000
        assert team.total_points == 118
        assert team.points_on_bench == 3
        assert team.squad_gameweek == 2
    finally:
        _cleanup(conn)
        conn.close()


def test_the_xi_bench_and_armband_are_ingested():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        team = get_my_team(conn, TEST_TEAM_ID)

        by_name = {p.name: p for p in team.players}
        assert by_name["Salah"].is_captain is True
        assert by_name["Salah"].squad_position == 1
        assert by_name["Haaland"].is_vice_captain is True
        # position 12 is the first bench slot
        assert by_name["Saka"].squad_position == 12
        # Ordered XI-then-bench, so the model reads a lineup rather than a set.
        assert [p.squad_position for p in team.players] == [1, 2, 12]
    finally:
        _cleanup(conn)
        conn.close()


def test_chips_already_played_are_ingested_from_entry_history():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = _ingest_once(conn)
        assert summary.chips_used == 1
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, gameweek FROM chips_used WHERE team_id = %s", (TEST_TEAM_ID,)
            )
            assert cur.fetchall() == [{"name": "wildcard", "gameweek": 1}]
    finally:
        _cleanup(conn)
        conn.close()


def test_pool_minutes_come_from_bootstrap_with_no_extra_calls():
    """The cheap tier of minutes security: season totals for the WHOLE pool,
    out of a payload ingestion already fetches every cycle."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, minutes, starts FROM fpl_players WHERE id = ANY(%s) ORDER BY id",
                (list(TEST_ELEMENT_IDS),),
            )
            rows = {r["id"]: r for r in cur.fetchall()}
        assert rows[101]["minutes"] == 180
        assert rows[103]["starts"] == 0
    finally:
        _cleanup(conn)
        conn.close()


def test_element_summary_is_fetched_for_the_squad_only_never_the_pool():
    """The expensive tier is one HTTP call per player. Fanning it across the
    pool would turn ingestion into a multi-minute job, so the squad bound is
    asserted rather than trusted."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = _ingest_once(conn)

        assert summary.minutes_players_fetched == 3
        assert len(element_summary_calls) == 3
        for element_id in TEST_ELEMENT_IDS:
            assert any(str(element_id) in call for call in element_summary_calls)
    finally:
        _cleanup(conn)
        conn.close()


def test_running_ingestion_twice_keeps_the_new_state_and_refetches_nothing():
    """Issue 10's cascade lesson, re-checked for every column and table issue
    20 adds -- and the minutes cache's whole reason to exist."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        element_summary_calls.clear()
        second = _ingest_once(conn)

        team = get_my_team(conn, TEST_TEAM_ID)
        assert team.finances.bank == 0.5
        assert team.overall_rank == 250_000
        assert second.chips_used == 1
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM chips_used WHERE team_id = %s", (TEST_TEAM_ID,))
            assert cur.fetchone()["n"] == 1
            cur.execute(
                "SELECT count(*) AS n FROM player_gameweek_history WHERE fpl_player_id = ANY(%s)",
                (list(TEST_ELEMENT_IDS),),
            )
            assert cur.fetchone()["n"] > 0
        # Cache held: the second cycle made no element-summary calls at all.
        assert second.minutes_players_fetched == 0
        assert element_summary_calls == []
    finally:
        _cleanup(conn)
        conn.close()


def test_per_gameweek_points_and_rank_history_are_ingested():
    """From the same /entry/{id}/history/ response the chips come from, so it
    costs no extra call. Rank is standing context that should move risk
    appetite, not bookkeeping."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        summary = _ingest_once(conn)
        assert summary.gameweeks_of_history == 1
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gameweek, points, overall_rank FROM entry_gameweek_history "
                "WHERE team_id = %s ORDER BY gameweek",
                (TEST_TEAM_ID,),
            )
            assert cur.fetchall() == [
                {"gameweek": 1, "points": 57, "overall_rank": 300_000}
            ]
    finally:
        _cleanup(conn)
        conn.close()


def test_history_upsert_does_not_duplicate_across_runs():
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        _ingest_once(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM entry_gameweek_history WHERE team_id = %s",
                (TEST_TEAM_ID,),
            )
            assert cur.fetchone()["n"] == 1
    finally:
        _cleanup(conn)
        conn.close()


def test_get_player_fetches_per_fixture_minutes_on_demand():
    """AC 7's second half: the user naming a player is the signal that his
    per-fixture history is worth one element-summary call. Bounded to that one
    player -- the pool is never fanned out."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        element_summary_calls.clear()

        player = get_player(conn, "Saka", _fake_client())

        # Newest gameweek first (recent_minutes orders DESC), so the two
        # rounds in ELEMENT_SUMMARY come back as [2, 1].
        assert [m.gameweek for m in player.recent_minutes] == [2, 1]
        assert player.recent_minutes[0].minutes == 90
        # Exactly one player looked up, not the pool.
        assert len(element_summary_calls) <= 1
    finally:
        _cleanup(conn)
        conn.close()


def test_get_player_without_a_client_stays_on_season_totals():
    """Every pre-existing caller passes no client and must keep working with
    no HTTP boundary at all."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    try:
        _ingest_once(conn)
        player = get_player(conn, "Saka")

        assert player.recent_minutes == []
        assert player.minutes == 100
    finally:
        _cleanup(conn)
        conn.close()
