import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

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
    requirement), and cleans up afterwards so the shared Supabase project
    stays empty between test runs."""
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
