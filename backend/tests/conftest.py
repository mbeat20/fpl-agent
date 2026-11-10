import os
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

# ---------------------------------------------------------------------------
# The test-database guard.
#
# Until 2026-08-30 this file connected to whatever `DATABASE_URL` held, which
# was the live Supabase project the dashboard reads from. `seeded_team` below
# does `DELETE FROM teams WHERE id = MY_TEAM_ID` -- the REAL team id, 1 -- and
# `test_ingestion.py` does a bare `DELETE FROM fixtures`. So running the suite
# wiped the user's squad, cached suggestion, instincts and outcome history,
# and replaced the squad with a three-player placeholder. That happened five
# times across four sessions, twice after a memory note warning about it in
# bold, which is the clearest possible evidence that discipline was never
# going to be the fix.
#
# So the suite now refuses to start unless it has somewhere safe to run.
#
# `TEST_DATABASE_URL` points at the `fpl_test` SCHEMA on the same Supabase
# project, connecting as a dedicated `fpl_test_runner` role. Two properties
# make that genuinely safe rather than merely conventional:
#
# - The role's default search_path is set SERVER-side
#   (`ALTER ROLE ... SET search_path = fpl_test, public`). A
#   `?options=-csearch_path=...` on the connection URL is silently dropped by
#   Supabase's Supavisor pooler -- verified, and it fails open, which is the
#   worst possible way to fail.
# - The role holds NO privileges on any table in `public`. A fall-through is
#   therefore a permission error, not a silent write to real data. That is the
#   actual guarantee here; the search_path is just ergonomics.
#
# Point TEST_DATABASE_URL at a wholly separate project instead and everything
# below still works -- the schema trick is the cheap implementation of the
# rule, not the rule.
# ---------------------------------------------------------------------------

_LIVE_OVERRIDE = "FPL_ALLOW_LIVE_DB"

_NO_TEST_DB = f"""
Refusing to run: TEST_DATABASE_URL is not set.

This suite is destructive. `seeded_team` deletes team {1} and cascades through
players, gameweek_state, instincts, recommendation_log, team_suggestions and
roadmaps; test_ingestion.py additionally empties the global `fixtures` table.
Against the live database that destroys real data, some of which ingestion
cannot restore.

Set TEST_DATABASE_URL in backend/.env (see docs/adr/0017-a-test-database.md),
or, if you genuinely mean to run against DATABASE_URL, set {_LIVE_OVERRIDE}=1.
"""

_SAME_URL = f"""
Refusing to run: TEST_DATABASE_URL and DATABASE_URL are identical.

That defeats the point -- the suite would still be writing to the live
database. Give the test URL its own schema-scoped role (or its own project).
Set {_LIVE_OVERRIDE}=1 to override deliberately.
"""


def _select_database() -> None:
    """Redirect every connection in the suite to the test database.

    Done by overwriting the environment variable rather than by threading a
    URL through fixtures, because tests call `psycopg.connect(
    os.environ["DATABASE_URL"])` directly in a dozen files and
    `fpl_coach.db.get_connection` reads the same variable at call time. One
    assignment here covers all of them, including the app under test.
    """
    if os.environ.get(_LIVE_OVERRIDE) == "1":
        return

    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        raise RuntimeError(_NO_TEST_DB)
    if test_url == os.environ.get("DATABASE_URL"):
        raise RuntimeError(_SAME_URL)

    os.environ["DATABASE_URL"] = test_url


_select_database()

from fpl_coach.api.routes import MY_TEAM_ID, get_reasoning  # noqa: E402
from fpl_coach.app import app  # noqa: E402
from fpl_coach.reasoning.fake import FakeReasoning  # noqa: E402

SCHEMA_SQL = (Path(__file__).parent.parent / "schema.sql").read_text()

SEED_PLAYERS = [
    ("Mohamed Salah", "MID", "Liverpool", 13.5),
    ("Erling Haaland", "FWD", "Man City", 15.0),
    ("Bukayo Saka", "MID", "Arsenal", 10.0),
]


@pytest.fixture(scope="session", autouse=True)
def _apply_schema():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(SCHEMA_SQL)


@pytest.fixture
def seeded_team():
    """Seeds one Team + its Players (real Postgres, per the ticket's Seam-1
    requirement), and cleans up afterwards so the test database stays empty
    between test runs.

    Still uses MY_TEAM_ID rather than a distinct test id. That was a hazard
    while the suite ran against the live database -- it made the fixture
    delete the user's actual team -- and is now merely a detail, because the
    only `teams` table this can reach is the test one. Left alone rather than
    changed for its own sake: the routes under test are bound to MY_TEAM_ID by
    closure, so a different id here would mean threading an override through
    every route test to prove nothing extra.
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (MY_TEAM_ID,))
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (MY_TEAM_ID, "Test Team"))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO players (team_id, name, position, club, price) "
                "VALUES (%s, %s, %s, %s, %s)",
                [(MY_TEAM_ID, *player) for player in SEED_PLAYERS],
            )

    yield MY_TEAM_ID

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM teams WHERE id = %s", (MY_TEAM_ID,))


@pytest.fixture
def client():
    """TestClient wired to the deterministic FakeReasoning double (Seam-1)."""
    app.dependency_overrides[get_reasoning] = lambda: FakeReasoning()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def fake_message_usage():
    """The `usage` object every real Messages response carries.

    Four separate token classes, because they bill at four different rates and
    the reasoning loop logs them separately to show whether its cache
    breakpoints are working. Zeros here: the tests that use this assert on
    decisions, not on cost.
    """
    return SimpleNamespace(
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def fake_message_response(blocks, stop_reason="tool_use"):
    """A stand-in for one `client.messages.create` return value.

    Shared rather than redefined per test module: it stood in four places, and
    when the loop started reading a new field off the response all four had to
    learn about it at once or fail as a set.
    """
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, usage=fake_message_usage()
    )
