"""The ingestion run ledger and the refresh endpoints.

The behaviours worth defending here are the ones that cost money or lie:

- a second concurrent run of the same kind is refused, because the creators
  pipeline bills per run;
- a crashed run does not block refreshes forever;
- freshness never reports an age it does not have, and says when its number
  came from the data's own timestamps rather than from a recorded run.

No test here touches a real pipeline. `execute_run` is exercised against a
stubbed runner, so the ledger's behaviour is tested without spending anything.
"""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from fpl_coach.api.routes import MY_TEAM_ID
from fpl_coach.db import get_connection
from fpl_coach.ingestion import jobs
from fpl_coach.ingestion.pipeline import IngestionSummary
from fpl_coach.ingestion.runs import (
    ABANDON_AFTER,
    RunAlreadyRunning,
    fail_run,
    finish_run,
    freshness,
    ingestion_status,
    latest_run,
    reap_abandoned,
    start_run,
)


@pytest.fixture
def clean_runs():
    """The ledger has no FK to teams (see schema.sql), so `seeded_team`'s
    cascade does not clear it -- these rows have to be removed explicitly or
    they leak into the next test's freshness reads."""
    _wipe()
    yield
    _wipe()


def _wipe() -> None:
    with psycopg.connect(__import__("os").environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM ingestion_runs WHERE team_id = %s", (MY_TEAM_ID,))


def _summary() -> IngestionSummary:
    return IngestionSummary(
        team_name="Test Team",
        players_ingested=15,
        fixtures_ingested=380,
        players_pool_ingested=700,
        players_with_xg=650,
        outcomes_recorded=12,
    )


# ---------------------------------------------------------------------------
# The lock


def test_start_run_claims_the_lock(clean_runs):
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")

    assert run.status == "running"
    assert run.kind == "fpl"
    assert run.finished_at is None
    # Committed before returning: the status endpoint has to be able to see
    # the claim the moment the POST responds.
    with get_connection() as other:
        assert latest_run(other, MY_TEAM_ID, "fpl").id == run.id


def test_second_run_of_the_same_kind_is_refused(clean_runs):
    with get_connection() as conn:
        first = start_run(conn, MY_TEAM_ID, "fpl")

        with pytest.raises(RunAlreadyRunning) as excinfo:
            start_run(conn, MY_TEAM_ID, "fpl")

    # The refusal carries the run that holds the lock, so the caller can say
    # "started 4 minutes ago" rather than just "conflict".
    assert excinfo.value.run.id == first.id


def test_the_two_kinds_do_not_block_each_other(clean_runs):
    """The lock is per kind. A free FPL refresh must not be gated behind a
    twenty-minute creator run that happens to be in flight."""
    with get_connection() as conn:
        start_run(conn, MY_TEAM_ID, "creators")
        fpl = start_run(conn, MY_TEAM_ID, "fpl")

    assert fpl.status == "running"


def test_finishing_releases_the_lock(clean_runs):
    with get_connection() as conn:
        first = start_run(conn, MY_TEAM_ID, "fpl")
        finish_run(conn, first.id, _summary())
        second = start_run(conn, MY_TEAM_ID, "fpl")

    assert second.id != first.id


def test_failing_also_releases_the_lock(clean_runs):
    """A failed refresh is exactly when the user wants to press again."""
    with get_connection() as conn:
        first = start_run(conn, MY_TEAM_ID, "fpl")
        fail_run(conn, first.id, "ConnectionError: FPL API unreachable")
        second = start_run(conn, MY_TEAM_ID, "fpl")

    assert second.id != first.id
    with get_connection() as conn:
        assert latest_run(conn, MY_TEAM_ID, "fpl").id == second.id


# ---------------------------------------------------------------------------
# Recording what happened


def test_a_finished_run_stores_the_pipeline_summary_whole(clean_runs):
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")
        finish_run(conn, run.id, _summary())
        stored = latest_run(conn, MY_TEAM_ID, "fpl")

    assert stored.status == "succeeded"
    assert stored.summary["players_ingested"] == 15
    assert stored.summary["outcomes_recorded"] == 12
    assert stored.duration_seconds is not None


def test_latest_run_reports_a_failure_rather_than_the_last_success(clean_runs):
    """A failure after the last success is the most important thing on the
    screen. Filtering to successes would hide it behind a green stamp."""
    with get_connection() as conn:
        ok = start_run(conn, MY_TEAM_ID, "fpl")
        finish_run(conn, ok.id, _summary())
        bad = start_run(conn, MY_TEAM_ID, "fpl")
        fail_run(conn, bad.id, "ConnectionError: FPL API unreachable")

        latest = latest_run(conn, MY_TEAM_ID, "fpl")

    assert latest.status == "failed"
    assert "ConnectionError" in latest.error


# ---------------------------------------------------------------------------
# Crash recovery


def test_a_long_running_row_is_reaped_as_abandoned(clean_runs):
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "creators")
        _age(run.id, ABANDON_AFTER + timedelta(minutes=5))

        assert reap_abandoned(conn, MY_TEAM_ID) == 1
        reaped = latest_run(conn, MY_TEAM_ID, "creators")

    # `abandoned`, not `failed`: it stopped reporting, which is not the same
    # as having stopped working, and it may well have written most of its data.
    assert reaped.status == "abandoned"
    assert "abandoned" in reaped.error


def test_reaping_frees_the_lock_so_a_crash_is_not_permanent(clean_runs):
    """Without this, a run killed by a uvicorn reload blocks every future
    refresh forever -- silently, which makes it worse than the double-run the
    lock exists to prevent."""
    with get_connection() as conn:
        crashed = start_run(conn, MY_TEAM_ID, "fpl")
        _age(crashed.id, ABANDON_AFTER + timedelta(minutes=1))

        fresh = start_run(conn, MY_TEAM_ID, "fpl")

    assert fresh.id != crashed.id
    assert fresh.status == "running"


def test_a_recent_running_row_is_left_alone(clean_runs):
    """Reaping a live run frees the lock and the next refresh starts a SECOND
    concurrent pass over a billed pipeline. Too-eager is the expensive
    direction."""
    with get_connection() as conn:
        start_run(conn, MY_TEAM_ID, "creators")
        assert reap_abandoned(conn, MY_TEAM_ID) == 0
        assert latest_run(conn, MY_TEAM_ID, "creators").status == "running"


# ---------------------------------------------------------------------------
# Freshness


def test_freshness_says_none_when_nothing_was_ever_ingested(clean_runs, seeded_team):
    """`none` is not the same as old. Rendering it as an age would invent a
    refresh that never happened."""
    with get_connection() as conn:
        conn.execute("DELETE FROM gameweek_state WHERE team_id = %s", (MY_TEAM_ID,))
        conn.commit()
        result = freshness(conn, MY_TEAM_ID, "fpl")

    assert result.source == "none"
    assert result.last_success is None
    assert result.age_seconds is None
    assert result.is_stale is False
    assert "missing rather than out of date" in result.note


def test_freshness_uses_a_recorded_run_when_there_is_one(clean_runs, seeded_team):
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")
        finish_run(conn, run.id, _summary())
        result = freshness(conn, MY_TEAM_ID, "fpl")

    assert result.source == "run"
    assert result.age_seconds < 120
    assert result.is_stale is False
    assert "last refreshed successfully" in result.note


def test_freshness_falls_back_to_the_datas_own_timestamp(clean_runs, seeded_team):
    """Every run made before the ledger existed lands here. Claiming "never
    refreshed" while sitting on a full database is the failure this avoids --
    and the note says the figure is a lower bound, because a run that found
    nothing new would not have moved it."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET updated_at = EXCLUDED.updated_at",
            (MY_TEAM_ID, 5, datetime.now(timezone.utc) - timedelta(hours=3)),
        )
        conn.commit()
        result = freshness(conn, MY_TEAM_ID, "fpl")

    assert result.source == "proxy"
    assert 2.5 * 3600 < result.age_seconds < 3.5 * 3600
    assert "No ingestion run is on record" in result.note


def test_freshness_flags_data_older_than_a_gameweek(clean_runs, seeded_team):
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")
        finish_run(conn, run.id, _summary())
        _finish_age(run.id, timedelta(days=9))
        result = freshness(conn, MY_TEAM_ID, "fpl")

    assert result.is_stale is True
    assert "out of date" in result.note


def test_status_reaps_before_reporting(clean_runs, seeded_team):
    """Recovery rides on the user opening the page -- the only reliable
    trigger available without the scheduler ADR-0002 ruled out."""
    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "creators")
        _age(run.id, ABANDON_AFTER + timedelta(minutes=1))

        status = ingestion_status(conn, MY_TEAM_ID)

    assert status.running == []
    assert [r.status for r in status.runs] == ["abandoned"]
    assert {f.kind for f in status.freshness} == {"fpl", "creators"}


# ---------------------------------------------------------------------------
# execute_run: the ledger is closed out whatever the pipeline does


def test_execute_run_records_success_and_returns_the_summary(clean_runs, monkeypatch):
    monkeypatch.setitem(jobs._RUNNERS, "fpl", lambda team_id: _summary())

    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")

    returned = jobs.execute_run(run.id, "fpl", MY_TEAM_ID)

    assert returned.players_ingested == 15
    with get_connection() as conn:
        assert latest_run(conn, MY_TEAM_ID, "fpl").status == "succeeded"


def test_execute_run_records_a_crash_instead_of_escaping(clean_runs, monkeypatch):
    """An escaping exception in a background task leaves the row `running`
    forever, and there is nobody to show the traceback to."""

    def boom(team_id):
        raise RuntimeError("FPL API returned 503")

    monkeypatch.setitem(jobs._RUNNERS, "fpl", boom)

    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")

    assert jobs.execute_run(run.id, "fpl", MY_TEAM_ID) is None

    with get_connection() as conn:
        recorded = latest_run(conn, MY_TEAM_ID, "fpl")
    assert recorded.status == "failed"
    assert "RuntimeError: FPL API returned 503" in recorded.error
    # The failing frame, so a bug report says where rather than only what.
    assert "in boom" in recorded.error


def test_execute_run_reraises_for_the_cli_after_recording(clean_runs, monkeypatch):
    """A terminal wants the stack. The row is written first either way, so
    the ledger is correct regardless of which the caller asked for."""

    def boom(team_id):
        raise RuntimeError("FPL API returned 503")

    monkeypatch.setitem(jobs._RUNNERS, "fpl", boom)

    with get_connection() as conn:
        run = start_run(conn, MY_TEAM_ID, "fpl")

    with pytest.raises(RuntimeError):
        jobs.execute_run(run.id, "fpl", MY_TEAM_ID, reraise=True)

    with get_connection() as conn:
        assert latest_run(conn, MY_TEAM_ID, "fpl").status == "failed"


# ---------------------------------------------------------------------------
# The endpoints


def test_status_endpoint_is_a_read(client, clean_runs, seeded_team):
    body = client.get("/ingest/status").json()

    assert body["running"] == []
    assert {f["kind"] for f in body["freshness"]} == {"fpl", "creators"}


def test_post_returns_202_and_schedules_the_work(client, clean_runs, monkeypatch):
    """202 rather than 200: a creator run can take tens of minutes, so the
    response is an acknowledgement and the run's fate must not depend on the
    tab staying open."""
    monkeypatch.setitem(jobs._RUNNERS, "fpl", lambda team_id: _summary())

    res = client.post("/ingest/fpl")

    assert res.status_code == 202
    body = res.json()
    assert body["kind"] == "fpl"
    # TestClient runs background tasks before returning, so by now it is done.
    with get_connection() as conn:
        assert latest_run(conn, MY_TEAM_ID, "fpl").status == "succeeded"


def test_post_conflicts_while_a_run_is_in_flight(client, clean_runs):
    with get_connection() as conn:
        held = start_run(conn, MY_TEAM_ID, "creators")

    res = client.post("/ingest/creators")

    assert res.status_code == 409
    detail = res.json()["detail"]
    # The holding run travels with the refusal, so the UI can say since when.
    assert detail["run"]["id"] == held.id
    assert "already in progress" in detail["message"]
    assert detail["message"].startswith("A creator refresh")


def test_post_rejects_an_unknown_kind(client, clean_runs):
    res = client.post("/ingest/everything")

    assert res.status_code == 404
    assert "Unknown ingestion kind" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Helpers that reach past the module's own API to fake the passage of time.


def _age(run_id: int, by: timedelta) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_runs SET started_at = started_at - %s WHERE id = %s",
            (by, run_id),
        )
        conn.commit()


def _finish_age(run_id: int, by: timedelta) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_runs SET finished_at = finished_at - %s WHERE id = %s",
            (by, run_id),
        )
        conn.commit()
