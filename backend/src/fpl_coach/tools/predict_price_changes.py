from psycopg import Connection

from fpl_coach.models import PriceChangePredictions, PricePrediction
from fpl_coach.reasoning.base import ToolSpec

_DEFAULT_LIMIT = 10

_CANDIDATES_SQL = (
    "SELECT id, name, ownership, transfers_in_event, transfers_out_event, "
    "cost_change_event FROM fpl_players "
    "WHERE transfers_in_event IS NOT NULL AND transfers_out_event IS NOT NULL"
)


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


def _to_prediction(row: dict, net: int, direction: str) -> PricePrediction:
    return PricePrediction(
        player_id=row["id"],
        player_name=row["name"],
        direction=direction,
        ownership=row["ownership"],
        net_transfers_event=net,
        evidence=_evidence(row["ownership"], net, row["cost_change_event"]),
    )


def _rank(rows: list[dict], limit: int | None) -> PriceChangePredictions:
    """Split rows into rising and falling, most-likely-first.

    Shared by the whole-pool tool and issue 23's squad-only lookup so the two
    can never disagree about which way a player is heading -- a dashboard
    that says "falling" beside a model that says "rising" is worse than
    either one alone.
    """
    candidates = []
    for row in rows:
        net = row["transfers_in_event"] - row["transfers_out_event"]
        if net == 0:
            continue
        candidates.append((row, net, _score(row["ownership"], net)))

    rising = sorted((c for c in candidates if c[1] > 0), key=lambda c: c[2], reverse=True)
    falling = sorted((c for c in candidates if c[1] < 0), key=lambda c: c[2])
    if limit is not None:
        rising, falling = rising[:limit], falling[:limit]

    return PriceChangePredictions(
        rising=[_to_prediction(row, net, "rise") for row, net, _ in rising],
        falling=[_to_prediction(row, net, "fall") for row, net, _ in falling],
    )


def predict_price_changes(conn: Connection, limit: int = _DEFAULT_LIMIT) -> PriceChangePredictions:
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES_SQL)
        rows = cur.fetchall()
    return _rank(rows, limit)


def price_changes_for(conn: Connection, player_ids: list[int]) -> PriceChangePredictions:
    """The same call, narrowed to a given set of players and unlimited.

    Issue 23 needs price movement on the user's OWN squad, and the top-10
    slice above cannot answer that: a squad player heading for a fall is
    usually nowhere near the ten steepest falls in the game, which is exactly
    why filtering the tool's output would have quietly reported "no risk".
    The limit is dropped rather than raised for the same reason -- fifteen
    players is already the bound.
    """
    if not player_ids:
        return PriceChangePredictions(rising=[], falling=[])
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES_SQL + " AND id = ANY(%s)", (list(player_ids),))
        rows = cur.fetchall()
    return _rank(rows, limit=None)


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
