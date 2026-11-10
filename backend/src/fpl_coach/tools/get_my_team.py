from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek
from fpl_coach.models import Player, Team
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.squad_state import gameweeks_played, rotation_risk, sellable_value

def get_my_team(conn: Connection, team_id: int) -> Team:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, bank, team_value, event_transfers, event_transfers_cost, "
            "overall_rank, gameweek_points, total_points, points_on_bench, active_chip, "
            "squad_gameweek "
            "FROM teams WHERE id = %s",
            (team_id,),
        )
        team_row = cur.fetchone()
        if team_row is None:
            raise ValueError(f"No team with id {team_id}")

        cur.execute(
            # Issue 05: fpl_element_id (when set) puts squad players in the
            # same id namespace as the full pool (fpl_players.id), so a
            # Recommendation's pick can reference either uniformly. Falls
            # back to the row's own serial id for manually seeded rows.
            #
            # Issue 20: ordered by squad_position so the XI comes back before
            # the bench and the bench in the order it would come on. NULLS
            # LAST keeps manually seeded rows (no position) from leading.
            "SELECT COALESCE(fpl_element_id, id) AS id, name, position, club, price, "
            "ownership, form, status, news, minutes, starts, squad_position, "
            "is_captain, is_vice_captain, multiplier "
            "FROM players WHERE team_id = %s "
            "ORDER BY squad_position NULLS LAST, players.id",
            (team_id,),
        )
        player_rows = cur.fetchall()

    squad_gameweek = team_row["squad_gameweek"]
    played = gameweeks_played(conn)
    players = [
        Player(
            **row,
            rotation_risk=rotation_risk(row["minutes"], played, row["status"]),
        )
        for row in player_rows
    ]

    return Team(
        id=team_row["id"],
        name=team_row["name"],
        players=players,
        # planning_gameweek, not gameweek_state directly: this value is
        # what the reasoning layer labels its advice with, so it must be
        # the gameweek being planned rather than whatever ingestion last
        # persisted, which lags once a gameweek finishes.
        current_gameweek=planning_gameweek(conn, team_id),
        squad_gameweek=squad_gameweek,
        finances=sellable_value(conn, team_id),
        overall_rank=team_row["overall_rank"],
        gameweek_points=team_row["gameweek_points"],
        total_points=team_row["total_points"],
        points_on_bench=team_row["points_on_bench"],
        event_transfers=team_row["event_transfers"],
        event_transfers_cost=team_row["event_transfers_cost"],
        active_chip=team_row["active_chip"],
    )


def make_get_my_team_tool(conn: Connection, team_id: int) -> ToolSpec:
    """v1 is single-user (one team), so the tool takes no arguments -- it
    always returns that one seeded team, bound via closure."""
    return ToolSpec(
        name="get_my_team",
        description=(
            "Returns the user's full FPL state: team name, current_gameweek "
            "(the gameweek being planned -- the next one whose deadline has NOT "
            "passed, which is the one the user can still act on; null if not yet "
            "known -- issue 12: anchor a multi-week plan's step gameweeks to this), "
            "and each player's id, "
            "name, position, club, price, ownership %, form, and injury status "
            "(a=available, d=doubtful, i=injured, s=suspended, u=unavailable, "
            "n=not in squad; null for not-yet-ingested data).\n\n"
            "SQUAD SHAPE: squad_position 1-11 is the starting XI, 12-15 the bench in "
            "the order they would come on; is_captain and is_vice_captain carry the "
            "armband. These come from squad_gameweek, which TRAILS current_gameweek "
            "whenever the next deadline has not passed -- FPL only publishes picks "
            "after a deadline locks. Say 'as of GW<squad_gameweek>' rather than "
            "implying it is the lineup for the gameweek being planned.\n\n"
            "MONEY: `finances` carries bank, team_value (FPL's figure, which INCLUDES "
            "the bank) and squad_selling_value (the 15 players alone), all in millions. "
            "Budget for a transfer is bank + what the outgoing player sells for. Check "
            "finances.selling_prices_exact before calling any transfer affordable -- "
            "when it is false, FPL's real selling price may be below the listed "
            "price and the listed price is only an UPPER BOUND. Read finances.note "
            "and pass its caveat on to the user rather than asserting a transfer "
            "definitely fits.\n\n"
            "MINUTES: `minutes` and `starts` are season totals, and rotation_risk is "
            "true for an AVAILABLE player whose minutes average under 60 a gameweek. "
            "A rotation_risk player is a real risk even with no injury news -- treat "
            "it as evidence, and mention it when recommending or keeping them.\n\n"
            "Also: overall_rank, total_points, gameweek_points, points_on_bench, "
            "event_transfers (transfers already made this gameweek) and "
            "event_transfers_cost (points already spent on hits)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=lambda: get_my_team(conn, team_id),
    )
