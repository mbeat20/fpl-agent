from psycopg import Connection

from fpl_coach.models import PriceChangePredictions, PricePrediction
from fpl_coach.reasoning.base import ToolSpec

_DEFAULT_LIMIT = 10


def _score(ownership: float | None, net_transfers: int) -> float:
    """Net transfers alone favour high-ownership players, but FPL's own
    (undocumented) price-change threshold scales with a player's existing
    ownership base -- a low-ownership player needs far fewer raw transfers
    to move. Dividing by ownership approximates that relative significance
    instead of just raw transfer counts."""
    return net_transfers / (float(ownership or 0.0) + 1.0)


def _evidence(ownership: float | None, net_transfers: int, cost_change_event: int | None) -> str:
    sign = "+" if net_transfers > 0 else ""
    evidence = f"Net {sign}{net_transfers} transfers this gameweek ({float(ownership or 0.0)}% owned)"
    if cost_change_event:
        evidence += " -- price has already moved today"
    return evidence


def predict_price_changes(conn: Connection, limit: int = _DEFAULT_LIMIT) -> PriceChangePredictions:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, ownership, transfers_in_event, transfers_out_event, "
            "cost_change_event FROM fpl_players "
            "WHERE transfers_in_event IS NOT NULL AND transfers_out_event IS NOT NULL"
        )
        rows = cur.fetchall()

    candidates = []
    for row in rows:
        net = row["transfers_in_event"] - row["transfers_out_event"]
        if net == 0:
            continue
        candidates.append((row, net, _score(row["ownership"], net)))

    def to_prediction(row: dict, net: int, direction: str) -> PricePrediction:
        return PricePrediction(
            player_id=row["id"],
            player_name=row["name"],
            direction=direction,
            ownership=row["ownership"],
            net_transfers_event=net,
            evidence=_evidence(row["ownership"], net, row["cost_change_event"]),
        )

    rising = sorted((c for c in candidates if c[1] > 0), key=lambda c: c[2], reverse=True)
    falling = sorted((c for c in candidates if c[1] < 0), key=lambda c: c[2])

    return PriceChangePredictions(
        rising=[to_prediction(row, net, "rise") for row, net, _ in rising[:limit]],
        falling=[to_prediction(row, net, "fall") for row, net, _ in falling[:limit]],
    )


def make_predict_price_changes_tool(conn: Connection) -> ToolSpec:
    return ToolSpec(
        name="predict_price_changes",
        description=(
            "Returns players likely to rise or fall in price before it happens, ranked by "
            "net transfers this gameweek relative to each player's ownership (fewer transfers "
            "needed to move a low-ownership player's price than a high-ownership one). Each "
            "flagged player includes the net-transfer evidence behind the call."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "max players to return per direction (rise/fall), default 10",
                }
            },
        },
        handler=lambda limit=_DEFAULT_LIMIT: predict_price_changes(conn, limit),
    )
