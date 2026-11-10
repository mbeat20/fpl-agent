from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg import Connection

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.minutes import refresh_squad_minutes
from fpl_coach.outcomes import backfill_outcomes

_POSITION_BY_ELEMENT_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass(frozen=True)
class IngestionSummary:
    team_name: str
    players_ingested: int
    fixtures_ingested: int
    # Issue 05
    players_pool_ingested: int
    # ADR-0022: pool players carrying FPL's (Opta) season xG/xA.
    players_with_xg: int
    # Issue 10
    outcomes_recorded: int
    # Issue 20
    chips_used: int = 0
    minutes_players_fetched: int = 0
    gameweeks_of_history: int = 0
    # Issue 19
    gameweeks_of_calendar: int = 0
    # Issue 21
    clubs_ingested: int = 0


def _squad_gameweek(events: list[dict]) -> int:
    """The gameweek to pull the entry's actual picks from. FPL only exposes
    /entry/{id}/event/{gw}/picks/ for a gameweek whose deadline has passed,
    so this stays on bootstrap's is_current (falling back to is_next only
    pre-season, when no gameweek is current yet) -- deliberately NOT
    _planning_gameweek below, which can already point at a gameweek whose
    deadline hasn't landed and whose picks endpoint would 404."""
    for event in events:
        if event.get("is_current"):
            return event["id"]
    for event in events:
        if event.get("is_next"):
            return event["id"]
    raise ValueError("bootstrap-static returned no current or next gameweek")


def _planning_gameweek(events: list[dict], now: datetime | None = None) -> int:
    """The gameweek advice should target: the earliest one whose deadline
    hasn't passed, i.e. the one the user can still act on.

    NOT bootstrap's is_current. FPL leaves is_current on a gameweek until the
    next one kicks off, so between a gameweek's final whistle and the next
    deadline -- precisely the window in which anyone actually plans transfers
    and captaincy -- is_current names a gameweek that is already finished.
    Advising on it produces confidently obsolete answers (captain X against a
    fixture that has already been played)."""
    now = now or datetime.now(timezone.utc)
    upcoming = [
        (_deadline(event), event["id"])
        for event in events
        if event.get("deadline_time") and _deadline(event) > now
    ]
    if upcoming:
        return min(upcoming)[1]
    # Every deadline is in the past (season over, or a stale bootstrap):
    # fall back to the flags rather than inventing a gameweek.
    return _squad_gameweek(events)


def _tenths(value: int | None) -> float | None:
    """FPL money is in tenths of a million -- bank 5 means 0.5m."""
    return None if value is None else value / 10


def _latest_finished_gameweek(events: list[dict]) -> int | None:
    """The newest gameweek with results in, which is as far as any player's
    per-fixture history can possibly reach. Used as the minutes cache's
    freshness bound."""
    finished = [e["id"] for e in events if e.get("finished")]
    return max(finished) if finished else None


def _deadline(event: dict) -> datetime:
    return datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))


def _expected(element: dict, key: str) -> float | None:
    """FPL's season-cumulative Opta expected stat, sent as a decimal string
    ("4.06"). None only if the field is absent, so a player FPL simply has
    no figure for is unknown rather than scored as a zero (ADR-0022)."""
    value = element.get(key)
    return None if value is None else float(value)


def run_ingestion(
    conn: Connection,
    client: FPLClient,
    team_id: int,
    fpl_entry_id: int,
) -> IngestionSummary:
    """Replaces ticket-01's manually seeded Team/Players with the real thing,
    pulled on-demand from the official FPL API (ADR-0002: no scheduler), and
    (issue 05) refreshes the full player pool, including FPL's own xG/xA
    (ADR-0022, which replaced the Understat scrape)."""
    bootstrap = client.get_bootstrap()
    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}
    clubs_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    squad_gameweek = _squad_gameweek(bootstrap["events"])
    # What advice targets, persisted to gameweek_state below -- can be one
    # ahead of squad_gameweek once a gameweek has finished but the next
    # deadline hasn't passed.
    gameweek = _planning_gameweek(bootstrap["events"])

    entry = client.get_entry(fpl_entry_id)
    # Issue 20: the whole picks payload, not just `picks`. entry_history
    # carries bank/value/transfers and each pick carries its XI-or-bench
    # position, multiplier and armband -- all previously discarded.
    picks_payload = client.get_entry_picks(fpl_entry_id, squad_gameweek)
    picks = picks_payload["picks"]
    entry_history = picks_payload.get("entry_history") or {}
    active_chip = picks_payload.get("active_chip")
    history = client.get_entry_history(fpl_entry_id)
    fixtures = client.get_fixtures()

    with conn.cursor() as cur:
        # Issue 10: upsert, not delete+insert -- a delete+reinsert of the
        # teams row cascade-deletes gameweek_state/instincts/
        # recommendation_log (all FK'd to teams(id) ON DELETE CASCADE) on
        # every single ingestion cycle, silently wiping current-gameweek
        # Instincts and all outcome-logging history each time. players is
        # still wholesale-replaced, just via an explicit DELETE below rather
        # than piggybacking on the teams cascade.
        # Issue 20: entry state rides along on the same upsert. Still an
        # upsert, never delete+reinsert -- issue 10's cascade lesson applies
        # to every new teams-keyed table, chips_used included.
        cur.execute(
            """
            INSERT INTO teams
                (id, name, bank, team_value, event_transfers, event_transfers_cost,
                 overall_rank, gameweek_points, total_points, points_on_bench,
                 active_chip, squad_gameweek)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                bank = EXCLUDED.bank,
                team_value = EXCLUDED.team_value,
                event_transfers = EXCLUDED.event_transfers,
                event_transfers_cost = EXCLUDED.event_transfers_cost,
                overall_rank = EXCLUDED.overall_rank,
                gameweek_points = EXCLUDED.gameweek_points,
                total_points = EXCLUDED.total_points,
                points_on_bench = EXCLUDED.points_on_bench,
                active_chip = EXCLUDED.active_chip,
                squad_gameweek = EXCLUDED.squad_gameweek
            """,
            (
                team_id,
                entry["name"],
                _tenths(entry_history.get("bank")),
                _tenths(entry_history.get("value")),
                entry_history.get("event_transfers"),
                entry_history.get("event_transfers_cost"),
                entry_history.get("overall_rank"),
                entry_history.get("points"),
                entry_history.get("total_points"),
                entry_history.get("points_on_bench"),
                active_chip,
                squad_gameweek,
            ),
        )
        cur.execute("DELETE FROM players WHERE team_id = %s", (team_id,))

        # Issue 13: persist the resolved gameweek so Instinct logging/reading
        # (a chat-time path, not an ingestion one) never needs its own live
        # FPL API call, and drop any Instinct left over from a gameweek that
        # has since moved on.
        cur.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek, updated_at = now()",
            (team_id, gameweek),
        )
        cur.execute(
            "DELETE FROM instincts WHERE team_id = %s AND gameweek <> %s", (team_id, gameweek)
        )

        for pick in picks:
            element = elements_by_id[pick["element"]]
            cur.execute(
                """
                INSERT INTO players
                    (team_id, name, position, club, price, ownership, form, status, news,
                     fpl_element_id, squad_position, is_captain, is_vice_captain, multiplier,
                     minutes, starts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    team_id,
                    element["web_name"],
                    _POSITION_BY_ELEMENT_TYPE[element["element_type"]],
                    clubs_by_id[element["team"]],
                    element["now_cost"] / 10,
                    float(element["selected_by_percent"]),
                    float(element["form"]),
                    element["status"],
                    element["news"] or None,
                    element["id"],
                    pick.get("position"),
                    bool(pick.get("is_captain")),
                    bool(pick.get("is_vice_captain")),
                    pick.get("multiplier"),
                    element.get("minutes"),
                    element.get("starts"),
                ),
            )

        # Issue 20: chips already played. Replaced wholesale for this team --
        # the list is short and authoritative, and a chip cannot be un-played,
        # so reconciling deltas would be more code for the same result.
        cur.execute("DELETE FROM chips_used WHERE team_id = %s", (team_id,))
        chip_rows = [
            (team_id, chip["name"], chip["event"])
            for chip in history.get("chips", [])
            if chip.get("name") and chip.get("event") is not None
        ]
        if chip_rows:
            cur.executemany(
                "INSERT INTO chips_used (team_id, name, gameweek) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                chip_rows,
            )

        # Issue 20: the per-gameweek record, upserted rather than replaced --
        # a finished gameweek's points and rank never change, and rewriting
        # them every cycle would churn rows ADR-0012's history will read.
        history_rows = [
            (
                team_id,
                row["event"],
                row.get("points"),
                row.get("total_points"),
                row.get("overall_rank"),
                row.get("rank"),
                _tenths(row.get("bank")),
                _tenths(row.get("value")),
                row.get("event_transfers"),
                row.get("event_transfers_cost"),
                row.get("points_on_bench"),
            )
            for row in history.get("current", [])
            if row.get("event") is not None
        ]
        if history_rows:
            cur.executemany(
                """
                INSERT INTO entry_gameweek_history
                    (team_id, gameweek, points, total_points, overall_rank, gameweek_rank,
                     bank, team_value, event_transfers, event_transfers_cost, points_on_bench)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (team_id, gameweek) DO UPDATE SET
                    points = EXCLUDED.points,
                    total_points = EXCLUDED.total_points,
                    overall_rank = EXCLUDED.overall_rank,
                    gameweek_rank = EXCLUDED.gameweek_rank,
                    bank = EXCLUDED.bank,
                    team_value = EXCLUDED.team_value,
                    event_transfers = EXCLUDED.event_transfers,
                    event_transfers_cost = EXCLUDED.event_transfers_cost,
                    points_on_bench = EXCLUDED.points_on_bench
                """,
                history_rows,
            )

        # Issue 21: the club identity map. Same shape and same reasoning as
        # the gameweek calendar below -- global, upserted, and built from a
        # payload already in hand. Clubs are never deleted mid-season, and a
        # promoted/relegated set arrives as new ids at a season boundary, so
        # an upsert leaves last season's three rows behind rather than
        # breaking a foreign-key-less lookup that might still want them.
        # Falls back to the short name if `name` ever goes missing: the full
        # name is cosmetic (it exists so a question can say "Arsenal" where
        # the database says "ARS"), and the column is NOT NULL, so a payload
        # change would otherwise abort a whole ingestion run over a label.
        club_rows = [
            (club["id"], club.get("name") or club["short_name"], club["short_name"])
            for club in bootstrap.get("teams", [])
        ]
        if club_rows:
            cur.executemany(
                """
                INSERT INTO clubs (id, name, short_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    short_name = EXCLUDED.short_name
                """,
                club_rows,
            )

        # Issue 19: the gameweek calendar. Every field here was already in the
        # bootstrap payload fetched above and was being thrown away, so this
        # costs nothing. Upserted rather than replaced -- the deadlines table
        # is global, and a wholesale DELETE would leave a window in which a
        # concurrent read sees no calendar and silently falls back to the
        # unreliable stored gameweek tag.
        gameweek_rows = [
            (
                event["id"],
                event.get("name"),
                event["deadline_time"],
                bool(event.get("finished")),
                bool(event.get("is_current")),
                bool(event.get("is_next")),
            )
            for event in bootstrap["events"]
            if event.get("deadline_time")
        ]
        if gameweek_rows:
            cur.executemany(
                """
                INSERT INTO gameweeks
                    (id, name, deadline_time, finished, is_current, is_next)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    deadline_time = EXCLUDED.deadline_time,
                    finished = EXCLUDED.finished,
                    is_current = EXCLUDED.is_current,
                    is_next = EXCLUDED.is_next
                """,
                gameweek_rows,
            )

        cur.execute("DELETE FROM fixtures")
        for fixture in fixtures:
            cur.execute(
                """
                INSERT INTO fixtures
                    (id, gameweek, home_team, away_team, home_difficulty, away_difficulty,
                     kickoff_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    fixture["id"],
                    fixture["event"],
                    clubs_by_id[fixture["team_h"]],
                    clubs_by_id[fixture["team_a"]],
                    fixture["team_h_difficulty"],
                    fixture["team_a_difficulty"],
                    fixture["kickoff_time"],
                ),
            )

        cur.execute("DELETE FROM fpl_players")
        pool_rows = []
        players_with_xg = 0
        for element in bootstrap["elements"]:
            full_name = f"{element['first_name']} {element['second_name']}"
            xg = _expected(element, "expected_goals")
            xa = _expected(element, "expected_assists")
            if xg is not None and xa is not None:
                players_with_xg += 1
            pool_rows.append(
                (
                    element["id"],
                    element["web_name"],
                    full_name,
                    _POSITION_BY_ELEMENT_TYPE[element["element_type"]],
                    clubs_by_id[element["team"]],
                    element["now_cost"] / 10,
                    float(element["selected_by_percent"]),
                    float(element["form"]),
                    element["status"],
                    element["news"] or None,
                    xg,
                    xa,
                    element["transfers_in_event"],
                    element["transfers_out_event"],
                    element["cost_change_event"],
                    # Issue 20: already in the payload we fetch every cycle,
                    # so pool-wide minutes cost nothing extra.
                    element.get("minutes"),
                    element.get("starts"),
                    element.get("starts_per_90"),
                )
            )
        cur.executemany(
            """
            INSERT INTO fpl_players
                (id, name, full_name, position, club, price, ownership, form, status, news,
                 xg, xa, transfers_in_event, transfers_out_event, cost_change_event,
                 minutes, starts, starts_per_90)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            pool_rows,
        )

    conn.commit()

    # Issue 10 / ADR-0012, rewritten by issue 24: fills in the outcome of
    # EVERY Recommendation/Creator Stance still lacking one, however many
    # gameweeks back. It no longer reads this cycle's bootstrap elements --
    # those only carry "this event" numbers, which is what confined the old
    # implementation to a one-gameweek window and lost the history whenever an
    # ingestion cycle was skipped (ADR-0002: on-demand, not scheduled).
    # It resolves from player_gameweek_history instead, per gameweek, and is
    # driven by which rows lack an outcome rather than by what this run
    # touched. Bounded by the newest FINISHED gameweek, derived from fixtures
    # rather than passed in: a row for a gameweek still being played has no
    # outcome yet, and must not be scored as a nil.
    outcomes = backfill_outcomes(conn, client)
    outcomes_recorded = outcomes.resolved

    # Issue 20: per-fixture minutes for the squad only -- 15 calls at most,
    # and zero on a re-run within the same gameweek. Never the whole pool.
    minutes_fetched = refresh_squad_minutes(
        conn,
        client,
        [pick["element"] for pick in picks],
        _latest_finished_gameweek(bootstrap["events"]),
        clubs_by_id,
    )

    return IngestionSummary(
        team_name=entry["name"],
        players_ingested=len(picks),
        fixtures_ingested=len(fixtures),
        players_pool_ingested=len(pool_rows),
        players_with_xg=players_with_xg,
        outcomes_recorded=outcomes_recorded,
        chips_used=len(chip_rows),
        gameweeks_of_history=len(history_rows),
        minutes_players_fetched=minutes_fetched,
        gameweeks_of_calendar=len(gameweek_rows),
        clubs_ingested=len(club_rows),
    )
