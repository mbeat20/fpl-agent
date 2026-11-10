"""Issue 20: the parts of the user's FPL state that are not a list of players.

Money, chips, and minutes each have a rule the reasoning layer was already
being told to apply and had no data for. Two of them are answerable from the
public API; one is only partly answerable, and this module is where that line
is drawn honestly rather than papered over with a plausible-looking number.

**Money.** `entry_history` gives `bank` and `value` -- and `value` is FPL's
*team* value, the 15 selling prices PLUS the bank, not the squad alone. What
neither gives is per-player selling price -- that lives on
`/my-team/{id}/`, which answers 403 without an authenticated FPL session
(verified live, 2026-08-28). FPL sells a player for purchase price plus half
of any rise, and purchase price is simply not public. So rather than quietly
substituting current price -- which overstates the budget for any player who
has risen, and is exactly the error that makes an "affordable" transfer
bounce at the FPL end -- affordability is reported with an explicit
`selling_prices_exact` flag. See `sellable_value`.

**Chips.** `bootstrap.chips` is the full catalogue with each chip's own
start_event/stop_event window, and `/entry/{id}/history/` lists the ones
already played. Remaining chips are the difference, so nothing here hardcodes
a chip list that a rule change would silently invalidate.
"""

from psycopg import Connection

from fpl_coach.models import ChipStatus, ChipWindow, SquadFinances


# Issue 20: a fit player who is not playing. `status` only answers "is he
# injured/suspended", so a rotation risk was invisible until it showed up as
# bad form weeks later. Flagged rather than left for the model to infer,
# because the model cannot divide by a gameweek count it was never given.
#
# 60 minutes is FPL's own threshold for a full appearance point, so "averaging
# under an hour" is the game's own definition of not being a regular starter.
_ROTATION_MINUTES_PER_GAMEWEEK = 60
# Below this many total minutes there is not enough season yet to call anyone
# rotation-risky -- one blank in GW1 would flag the whole squad.
_MIN_SAMPLE_MINUTES = 90

# FPL money is in tenths, and float arithmetic on tenths needs a tolerance
# rather than an equality test.
_PRICE_TOLERANCE = 0.05


def gameweeks_played(conn: Connection) -> int | None:
    """How many gameweeks have actually kicked off, so season-total minutes
    can be turned into a per-gameweek average.

    Derived from fixtures rather than from gameweek_state or the squad's own
    gameweek: this is a fact about the season, not about one team's ingestion
    state, and the two diverge exactly when ingestion is stale.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(DISTINCT gameweek) AS played FROM fixtures "
            "WHERE gameweek IS NOT NULL AND kickoff_time <= now()"
        )
        row = cur.fetchone()
    return (row["played"] or None) if row else None


def latest_finished_gameweek(conn: Connection) -> int | None:
    """The newest gameweek whose fixtures have all kicked off -- as far as any
    player's per-fixture history can reach, and so the minutes cache's
    freshness bound. Derived from fixtures rather than passed in, so the
    on-demand path and the ingestion path cannot drift apart."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(gameweek) AS gw FROM fixtures "
            "WHERE gameweek IS NOT NULL AND kickoff_time <= now()"
        )
        row = cur.fetchone()
    return row["gw"] if row else None


def rotation_risk(minutes: int | None, played: int | None, status: str | None) -> bool:
    """True when an available player's minutes say he is not nailed.

    Deliberately scoped to players FPL considers available: an injured
    player's low minutes are already explained by `status`, and double-
    flagging them would bury the genuinely surprising case -- the fit player
    quietly being rotated.
    """
    if status is not None and status != "a":
        return False
    if minutes is None or not played or minutes < _MIN_SAMPLE_MINUTES:
        return False
    return (minutes / played) < _ROTATION_MINUTES_PER_GAMEWEEK


def sellable_value(conn: Connection, team_id: int) -> SquadFinances:
    """What the user can actually spend, and how confident we are in it.

    FPL's `value` is *team* value: the 15 selling prices PLUS the bank. At GW1
    every squad reads 100.0 with a 99.5 squad and 0.5 banked, which is the
    clearest possible demonstration -- subtracting the bank is not optional,
    and forgetting to overstates the squad by exactly the banked amount.

    Once the squad's selling total equals the sum of its *current* prices, no
    player has moved in price since purchase, so each player's selling price
    IS their listed price and affordability is exact. Early in a season it
    usually is.

    When they diverge we know the aggregate difference but cannot attribute it
    to individual players without the authenticated endpoint. Listed price
    then becomes an upper bound on what any one player raises, and the flag
    says so rather than letting the model treat a guess as a fact.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT bank, team_value FROM teams WHERE id = %s", (team_id,))
        team = cur.fetchone()
        cur.execute(
            "SELECT COALESCE(SUM(price), 0) AS total FROM players WHERE team_id = %s",
            (team_id,),
        )
        current_total = cur.fetchone()["total"]

    current = float(current_total or 0.0)

    if team is None or team["bank"] is None:
        return SquadFinances(
            current_price_total=current or None,
            selling_prices_exact=False,
            note=(
                "No bank or team value has been ingested yet -- run fpl-ingest. "
                "Do not estimate a budget; say plainly that it is unknown."
            ),
        )

    bank = float(team["bank"])
    team_value = float(team["team_value"]) if team["team_value"] is not None else None
    squad_selling = round(team_value - bank, 1) if team_value is not None else None

    exact = squad_selling is not None and abs(squad_selling - current) < _PRICE_TOLERANCE
    if exact:
        note = (
            f"Squad selling value (GBP {squad_selling}m) matches the sum of current "
            f"prices, so no player has changed price since purchase: each player's "
            f"selling price equals their listed price. With GBP {bank}m banked, "
            f"affordability figures are exact."
        )
    elif squad_selling is None:
        note = (
            f"GBP {bank}m banked, but no team value was ingested, so per-player "
            "selling prices cannot be checked. Treat listed prices as an upper bound."
        )
    else:
        shortfall = round(current - squad_selling, 1)
        note = (
            f"GBP {bank}m banked. The squad's real selling value is GBP "
            f"{squad_selling}m against GBP {current}m of listed prices -- a GBP "
            f"{shortfall}m gap, so at least one player sells for less than they are "
            "listed at. FPL does not publish per-player selling prices without an "
            "authenticated session, so treat each player's listed price as an UPPER "
            "BOUND on what selling them raises. Say so rather than presenting a "
            "transfer as definitely affordable when it is within that gap."
        )

    return SquadFinances(
        bank=bank,
        team_value=team_value,
        squad_selling_value=squad_selling,
        current_price_total=current,
        selling_prices_exact=exact,
        note=note,
    )


def chip_status(
    conn: Connection, team_id: int, chip_catalogue: list[dict], planning_gameweek: int | None
) -> ChipStatus:
    """Which chips are gone, which remain, and which can be played right now.

    `chip_catalogue` is bootstrap's `chips` array. FPL splits the season into
    two halves with a fresh wildcard/freehit/bboost/3xc in each, expressed as
    separate catalogue entries with different start/stop events -- which is
    why "remaining" is computed per catalogue entry rather than per chip name.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, gameweek FROM chips_used WHERE team_id = %s ORDER BY gameweek",
            (team_id,),
        )
        used_rows = cur.fetchall()

    used = [ChipWindow(name=r["name"], played_in_gameweek=r["gameweek"]) for r in used_rows]

    remaining: list[ChipWindow] = []
    for chip in chip_catalogue:
        start, stop = chip.get("start_event"), chip.get("stop_event")
        # A chip entry is spent if it was played inside its own window.
        spent = any(
            r["name"] == chip["name"]
            and start is not None
            and stop is not None
            and start <= r["gameweek"] <= stop
            for r in used_rows
        )
        if spent:
            continue
        playable = (
            planning_gameweek is not None
            and start is not None
            and stop is not None
            and start <= planning_gameweek <= stop
        )
        remaining.append(
            ChipWindow(
                name=chip["name"],
                available_from_gameweek=start,
                available_until_gameweek=stop,
                playable_now=playable,
            )
        )

    return ChipStatus(
        planning_gameweek=planning_gameweek,
        used=used,
        remaining=remaining,
        playable_now=[c.name for c in remaining if c.playable_now],
    )
