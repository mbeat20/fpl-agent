from dataclasses import dataclass

from psycopg import Connection

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.understat_client import UnderstatClient
from fpl_coach.outcomes import record_pending_outcomes

_POSITION_BY_ELEMENT_TYPE = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass(frozen=True)
class IngestionSummary:
    team_name: str
    players_ingested: int
    fixtures_ingested: int
    # Issue 05
    players_pool_ingested: int
    understat_season: str
    understat_matched: int
    # Issue 10
    outcomes_recorded: int


def _current_gameweek(events: list[dict]) -> int:
    for event in events:
        if event.get("is_current"):
            return event["id"]
    for event in events:
        if event.get("is_next"):
            return event["id"]
    raise ValueError("bootstrap-static returned no current or next gameweek")


def _understat_season_from_bootstrap(events: list[dict]) -> str:
    """Gameweek 1's deadline always falls in the new season's own start year
    (e.g. GW1 2025-08-16 -> season "2025"), so this needs no wall-clock
    guessing and stays correct regardless of when ingestion actually runs."""
    gw1 = next((e for e in events if e.get("id") == 1), None)
    if gw1 is None or not gw1.get("deadline_time"):
        raise ValueError("bootstrap-static has no gameweek-1 deadline to derive a season from")
    return gw1["deadline_time"][:4]


def _resolve_understat_players(
    client: UnderstatClient, season_guess: str
) -> tuple[str, list[dict]]:
    """Understat only has data for a season once it's actually started, so a
    freshly-rolled-over FPL season (bootstrap already on the new gameweek,
    but no matches played yet) can point at an Understat season that's still
    empty. Falls back one year in that case."""
    players = client.get_league_players(season_guess)
    if players:
        return season_guess, players
    fallback_season = str(int(season_guess) - 1)
    return fallback_season, client.get_league_players(fallback_season)


def _normalize_name(name: str) -> str:
    return " ".join(name.casefold().split())


def _xg_xa_by_name(understat_players: list[dict]) -> dict[str, tuple[float, float]]:
    return {
        _normalize_name(p["player_name"]): (float(p["xG"]), float(p["xA"]))
        for p in understat_players
    }


def run_ingestion(
    conn: Connection,
    client: FPLClient,
    understat_client: UnderstatClient,
    team_id: int,
    fpl_entry_id: int,
    understat_season: str | None = None,
) -> IngestionSummary:
    """Replaces ticket-01's manually seeded Team/Players with the real thing,
    pulled on-demand from the official FPL API (ADR-0002: no scheduler), and
    (issue 05) refreshes the full player pool with Understat xG/xA."""
    bootstrap = client.get_bootstrap()
    elements_by_id = {e["id"]: e for e in bootstrap["elements"]}
    clubs_by_id = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    gameweek = _current_gameweek(bootstrap["events"])

    entry = client.get_entry(fpl_entry_id)
    picks = client.get_entry_picks(fpl_entry_id, gameweek)["picks"]
    fixtures = client.get_fixtures()

    season_guess = understat_season or _understat_season_from_bootstrap(bootstrap["events"])
    resolved_season, understat_players = _resolve_understat_players(
        understat_client, season_guess
    )
    xg_xa_by_name = _xg_xa_by_name(understat_players)

    with conn.cursor() as cur:
        # Issue 10: upsert, not delete+insert -- a delete+reinsert of the
        # teams row cascade-deletes gameweek_state/instincts/
        # recommendation_log (all FK'd to teams(id) ON DELETE CASCADE) on
        # every single ingestion cycle, silently wiping current-gameweek
        # Instincts and all outcome-logging history each time. players is
        # still wholesale-replaced, just via an explicit DELETE below rather
        # than piggybacking on the teams cascade.
        cur.execute(
            "INSERT INTO teams (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            (team_id, entry["name"]),
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
                     fpl_element_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ),
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
        understat_matched = 0
        for element in bootstrap["elements"]:
            full_name = f"{element['first_name']} {element['second_name']}"
            xg_xa = xg_xa_by_name.get(_normalize_name(full_name))
            if xg_xa is not None:
                understat_matched += 1
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
                    xg_xa[0] if xg_xa else None,
                    xg_xa[1] if xg_xa else None,
                    element["transfers_in_event"],
                    element["transfers_out_event"],
                    element["cost_change_event"],
                )
            )
        cur.executemany(
            """
            INSERT INTO fpl_players
                (id, name, full_name, position, club, price, ownership, form, status, news,
                 xg, xa, transfers_in_event, transfers_out_event, cost_change_event)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            pool_rows,
        )

    conn.commit()

    # Issue 10 / ADR-0012: fills in the outcome of any Recommendation/Creator
    # Stance from an earlier gameweek that hasn't had one recorded yet, now
    # that this cycle's bootstrap-static elements are the freshest read of
    # what actually happened.
    outcomes_recorded = record_pending_outcomes(conn, elements_by_id, gameweek)

    return IngestionSummary(
        team_name=entry["name"],
        players_ingested=len(picks),
        fixtures_ingested=len(fixtures),
        players_pool_ingested=len(pool_rows),
        understat_season=resolved_season,
        understat_matched=understat_matched,
        outcomes_recorded=outcomes_recorded,
    )
