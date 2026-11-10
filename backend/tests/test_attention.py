"""Issue 28: the creator attention board.

Seeds its own creators, content and stances on a far-future gameweek so the
board can be asserted exactly. `creators`, `creator_content` and
`creator_stances` are global tables holding the real 19-creator roster, so
nothing here may depend on what they happen to contain -- every fixture row
carries a handle prefix this file owns, and the assertions are scoped to the
players it invented.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.attention import RECENT_HOURS, build_attention_board

TEST_TEAM_ID = 90225
HANDLE_PREFIX = "attention_test_"
PLANNING_GW = 120
CAL_YEAR = 2091
NOW = datetime(CAL_YEAR, 5, 8, 12, 0, tzinfo=timezone.utc)

# Deadlines either side of NOW, so GW120 is the gameweek being planned and
# GW119 is the one before it.
GW119_DEADLINE = datetime(CAL_YEAR, 5, 1, 17, 30, tzinfo=timezone.utc)
GW120_DEADLINE = datetime(CAL_YEAR, 5, 9, 17, 30, tzinfo=timezone.utc)

CREATORS = [f"{HANDLE_PREFIX}{n}" for n in range(1, 7)]


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle LIKE %s", (HANDLE_PREFIX + "%",))
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("DELETE FROM gameweeks WHERE id IN (119, 120)")


@pytest.fixture
def board_data():
    """Six creators, and three players with deliberately different shapes.

    Surger:  1 positive creator in GW119 -> 5 in GW120. A bandwagon.
    Faller:  5 positive in GW119 -> 1 in GW120. The same magnitude, downward.
    Steady:  3 positive in both. A settled view, and the row that must NOT
             outrank either of the movers.
    """
    _cleanup()
    with _conn() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Attn"))
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, PLANNING_GW),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO gameweeks (id, name, deadline_time) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET deadline_time = EXCLUDED.deadline_time",
                [(119, "Gameweek 119", GW119_DEADLINE), (120, "Gameweek 120", GW120_DEADLINE)],
            )

            creator_ids = []
            for handle in CREATORS:
                cur.execute(
                    "INSERT INTO creators (name, x_handle, trust_tier) "
                    "VALUES (%s, %s, 'high') RETURNING id",
                    (handle, handle),
                )
                creator_ids.append(cur.fetchone()["id"])

            def stance(creator_index, player, value, published):
                cur.execute(
                    "INSERT INTO creator_content (creator_id, source_url, content_text, "
                    "published_at) VALUES (%s, %s, 'text', %s) RETURNING id",
                    (
                        creator_ids[creator_index],
                        f"https://x.test/{player}/{creator_index}/{published.isoformat()}",
                        published,
                    ),
                )
                content_id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO creator_stances (creator_id, source_content_id, "
                    "player_name, stance, confidence, reason, gameweek) "
                    "VALUES (%s, %s, %s, %s, 0.8, 'because', %s)",
                    (creator_ids[creator_index], content_id, player, value, PLANNING_GW),
                )

            # Published before GW119's deadline -> attributed to GW119.
            old = GW119_DEADLINE - timedelta(days=1)
            # Published before GW120's deadline but a week ago -> GW120, not recent.
            current_old = GW119_DEADLINE + timedelta(hours=1)
            # Inside the 48-hour window.
            fresh = NOW - timedelta(hours=6)

            stance(0, "Surger", "buy", old)
            for i in range(5):
                stance(i, "Surger", "buy", fresh)

            for i in range(5):
                stance(i, "Faller", "buy", old)
            stance(0, "Faller", "buy", current_old)
            for i in range(1, 5):
                stance(i, "Faller", "sell", current_old)

            for i in range(3):
                stance(i, "Steady", "buy", old)
            for i in range(3):
                stance(i, "Steady", "buy", current_old)
        conn.commit()
        yield conn

    _cleanup()


def _row(board, name):
    return next(r for r in board.rows if r.player_name == name)


def test_the_board_ranks_by_movement_not_by_popularity(board_data):
    """The ordering argument. A settled three-creator view carries no news;
    a one-to-five swing does, and so does a five-to-one collapse."""
    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    ranked = [r.player_name for r in board.rows if r.player_name in {"Surger", "Faller", "Steady"}]
    assert ranked.index("Steady") > ranked.index("Surger")
    assert ranked.index("Steady") > ranked.index("Faller")


def test_a_collapse_ranks_alongside_a_surge(board_data):
    """Ranked on the ABSOLUTE delta. Ordering only on rises would quietly make
    the board a buy list, which is the thing ticket 19 refused to build."""
    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    surger, faller = _row(board, "Surger"), _row(board, "Faller")
    assert surger.change.positive == 4
    assert faller.change.positive == -4
    assert abs(surger.change.positive) == abs(faller.change.positive)


def test_movement_classification_comes_from_ticket_19(board_data):
    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    assert _row(board, "Surger").movement == "surging"
    assert _row(board, "Faller").movement == "cooling"
    assert _row(board, "Steady").movement == "settled"


def test_coverage_in_the_last_48_hours_is_separated_from_the_rest(board_data):
    """A theme running all week is background; one that appeared on Thursday
    is why the user opened the page."""
    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    surger, steady = _row(board, "Surger"), _row(board, "Steady")
    assert surger.creators_last_48h == 5
    # Steady's current-window stances were published a week before NOW.
    assert steady.creators_last_48h == 0
    assert steady.current.creators_covering == 3


def test_a_creator_posting_repeatedly_still_counts_once(board_data):
    """Six posts from one account is one creator. Surger's GW120 window has
    five creators across five posts; adding four more from one of them must
    not move the count."""
    with board_data.cursor() as cur:
        cur.execute(
            "SELECT id FROM creators WHERE x_handle = %s", (CREATORS[0],)
        )
        creator_id = cur.fetchone()["id"]
        for n in range(4):
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text, "
                "published_at) VALUES (%s, %s, 'text', %s) RETURNING id",
                (creator_id, f"https://x.test/spam/{n}", NOW - timedelta(hours=2)),
            )
            content_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO creator_stances (creator_id, source_content_id, player_name, "
                "stance, confidence, reason, gameweek) "
                "VALUES (%s, %s, 'Surger', 'buy', 0.8, 'again', %s)",
                (creator_id, content_id, PLANNING_GW),
            )
    board_data.commit()

    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)
    surger = _row(board, "Surger")

    assert surger.current.creators_covering == 5
    # ...but the noisier count still reflects how loudly it was said.
    assert surger.current.stances == 9


def test_the_board_says_what_it_is_a_slice_of(board_data):
    board = build_attention_board(board_data, TEST_TEAM_ID, limit=2, now=NOW)

    assert board.returned == 2
    assert len(board.rows) == 2
    assert board.total_players >= 3
    assert board.roster_size >= len(CREATORS)


def test_the_summary_is_ticket_19s_sentence(board_data):
    """Same prose on both surfaces, so the board and the per-player panel
    cannot describe one movement two different ways."""
    board = build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    assert "Surger" in _row(board, "Surger").summary
    assert _row(board, "Surger").summary


def test_attention_is_a_single_query_not_one_per_player(board_data, monkeypatch):
    """Ticket 19's per-player path is right for a handful of instinct-players
    and would be hundreds of round trips here."""
    executed = []
    real_execute = psycopg.Cursor.execute

    def counting_execute(self, query, params=None, **kwargs):
        executed.append(str(query))
        return real_execute(self, query, params, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", counting_execute)
    build_attention_board(board_data, TEST_TEAM_ID, now=NOW)

    stance_queries = [q for q in executed if "FROM creator_stances" in q]
    assert len(stance_queries) == 1


# ---------------------------------------------------------------------------
# The route


class _RefusingReasoning:
    def reason(self, prompt, tools, system="", validate=None, history=None):
        raise AssertionError("the attention board must not make a reasoning call")


def test_attention_route_is_a_free_read(board_data, monkeypatch):
    monkeypatch.setattr("fpl_coach.api.routes.MY_TEAM_ID", TEST_TEAM_ID)
    app.dependency_overrides[get_reasoning] = _RefusingReasoning
    try:
        with TestClient(app) as client:
            response = client.get("/creators/attention")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["planning_gameweek"] == PLANNING_GW
    assert {r["player_name"] for r in body["rows"]} >= {"Surger", "Faller"}


def test_recent_hours_is_two_days():
    """Pinned because the whole "new versus carried over" distinction is
    calibrated on it."""
    assert RECENT_HOURS == 48
