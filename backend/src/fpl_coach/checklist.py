"""Issue 23: the deadline screen -- how long is left, and what is checkably
wrong with the squad right now.

The dashboard already answers "what does the AI think", which is the question
the Ask tab answers. It does not answer the question the user actually has on
a Friday afternoon: **is anything about my team broken, and how long do I
have?** Everything needed to answer that is already in the database.

Three things this module is careful about.

**It states facts, it does not instruct.** Every `detail` string is a
statement -- "Two players in your XI are flagged: X (injured), Y (doubtful)"
-- never "bench X and Y". `preferences.md` is emphatic that the final call is
the user's, and a checklist that tells them what to do is a second, worse
recommendation engine competing with the real one three inches above it. The
`attention` state means "worth your eyes", not "you are wrong".

**It never spends money.** No Claude call, no live FPL API call: reads only,
so opening the dashboard is free. ADR-0002 keeps the AI suggestion on-demand
and cached (ticket 15's `team_suggestions`); this sits alongside it and must
not become a second reason to bill the user for looking at their own team.

**The countdown comes from the deadline calendar, not from the planning
gameweek.** The ticket asks for a countdown "derived from the planning
gameweek rather than the current one", and the point of that instruction is
to avoid bootstrap's `is_current`, which FPL leaves on a FINISHED gameweek
until the next kicks off. But `planning_gameweek` is not the right input
either, and for a reason worth writing down: it resolves to
`max(gameweek_state, upcoming_gameweek)`, and `upcoming_gameweek` is
"the earliest gameweek with an unplayed fixture". On a Saturday afternoon
that is the gameweek in progress -- whose deadline went on Friday. Counting
down to it would show a negative, or worse, count down to a deadline the user
has already missed.

So the countdown runs on `gameweeks.deadline_time` (issue 19's calendar):
the next deadline that has NOT passed is, definitionally, the next moment the
user can still act before. Both gameweeks are reported, and when they differ
the note says so plainly -- that difference IS the mid-gameweek state, and
hiding it would let the screen imply a locked team can still be changed.
"""

from datetime import datetime, timezone

from psycopg import Connection

from fpl_coach.gameweek import (
    load_deadlines,
    next_deadline,
    planning_gameweek as resolve_planning_gameweek,
)
from fpl_coach.models import (
    ChecklistItem,
    DeadlineChecklist,
    DeadlineCountdown,
    Player,
    PriceChangePredictions,
    Team,
)
from fpl_coach.tools.get_fixture_run import get_fixture_run
from fpl_coach.tools.get_my_team import get_my_team
from fpl_coach.tools.predict_price_changes import price_changes_for

# The same set get_injury_news treats as an injury risk. Kept in step with it
# deliberately: a player the risk summary calls flagged and the checklist
# calls fine is a contradiction the user has no way to resolve.
FLAGGED_STATUSES = {"d", "i", "s", "u"}

_STATUS_WORDS = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not in the FPL squad",
}

XI_SIZE = 11

# What counts as money sitting idle rather than money in transit. FPL squads
# routinely carry a few hundred thousand that no transfer can use, so a
# threshold below 1.0 would flag almost every squad every week and the item
# would stop meaning anything. At 1.0m and up there is a real upgrade the
# bank alone could fund.
IDLE_BANK = 1.0


def _status_word(status: str | None) -> str:
    return _STATUS_WORDS.get(status or "", "status unknown")


def _names(players: list[Player]) -> list[str]:
    return [player.name for player in players]


def _listing(players: list[Player]) -> str:
    return ", ".join(f"{p.name} ({_status_word(p.status)})" for p in players)


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


# ---------------------------------------------------------------------------
# The countdown


def countdown_notes(source: str, planning: int | None, gameweek: int) -> list[str]:
    """Everything the countdown has to say out loud about its own reliability.

    Pure, and separate from the query, because both cases here are ones the
    live database cannot be made to produce on demand: `first_kickoff` only
    happens when the gameweeks table is empty, and a planning gameweek ahead
    of the next deadline only happens mid-gameweek.
    """
    notes: list[str] = []

    if source == "first_kickoff":
        # The one failure mode on this screen that actually costs points: a
        # countdown that runs LONG. Said out loud rather than left in a field
        # nobody reads.
        notes.append(
            "This is the first kickoff of the gameweek, not the deadline itself -- "
            "the gameweeks calendar has not been ingested, so the real deadline is "
            "roughly 90 minutes EARLIER than shown. Treat the time left as an "
            "over-estimate until fpl-ingest has run."
        )

    if planning is not None and planning < gameweek:
        notes.append(
            f"GW{planning} is under way -- its deadline has passed, so that team is "
            f"locked. The next deadline you can act before is GW{gameweek}'s."
        )
    elif planning is not None and planning > gameweek:
        notes.append(
            f"Ingestion has resolved GW{planning}, but the next unpassed deadline on "
            f"the calendar is GW{gameweek}'s. One of the two is stale."
        )

    return notes


def deadline_countdown(
    conn: Connection, team_id: int, now: datetime | None = None
) -> DeadlineCountdown:
    """Time remaining until the next deadline the user can still act before."""
    now = now or datetime.now(timezone.utc)
    deadlines, source = load_deadlines(conn)
    planning = resolve_planning_gameweek(conn, team_id)
    upcoming = next_deadline(deadlines, now)

    if upcoming is None:
        note = (
            "No gameweek deadlines have been ingested yet -- run fpl-ingest."
            if source == "none"
            else "Every deadline on record has passed. The season is over, or the "
            "calendar needs re-ingesting."
        )
        return DeadlineCountdown(planning_gameweek=planning, source=source, note=note)

    gameweek, deadline = upcoming
    return DeadlineCountdown(
        gameweek=gameweek,
        planning_gameweek=planning,
        deadline=deadline.isoformat(),
        seconds_remaining=int((deadline - now).total_seconds()),
        source=source,
        note=" ".join(countdown_notes(source, planning, gameweek)) or None,
    )


# ---------------------------------------------------------------------------
# The items
#
# Each takes what it needs and returns one ChecklistItem. Split out one per
# fact rather than accumulated in a loop so that a check whose data is missing
# returns `unknown` on its own line instead of quietly dropping out of the
# list -- an absent item reads as a passed one.


def captain_item(xi: list[Player], squad_gameweek: int | None, target: int | None) -> ChecklistItem:
    if not xi:
        return ChecklistItem(
            key="captain",
            title="Captain",
            state="unknown",
            detail=(
                "No squad picks have been ingested, so the armband is not known. "
                "Run fpl-ingest."
            ),
        )

    captains = [player for player in xi if player.is_captain]

    # The honest caveat, and the reason this item can never simply say "yes,
    # you are fine": FPL only publishes picks once a deadline has locked, so
    # what we hold is the team from squad_gameweek, not a confirmed choice for
    # the gameweek being planned.
    stale = squad_gameweek is not None and target is not None and squad_gameweek < target
    as_of = f" as of GW{squad_gameweek}" if squad_gameweek is not None else ""
    caveat = (
        f" FPL only publishes picks after a deadline locks, so this is your GW"
        f"{squad_gameweek} team -- not a confirmed pick for GW{target}."
        if stale
        else ""
    )

    if len(captains) == 1:
        captain = captains[0]
        flag = (
            f" They are currently {_status_word(captain.status)}."
            if captain.status in FLAGGED_STATUSES
            else ""
        )
        return ChecklistItem(
            key="captain",
            title="Captain",
            state="attention" if captain.status in FLAGGED_STATUSES else "ok",
            detail=f"{captain.name} has the armband{as_of}.{flag}{caveat}",
            players=[captain.name],
        )

    if not captains:
        return ChecklistItem(
            key="captain",
            title="Captain",
            state="attention",
            detail=f"No player in the XI carries the armband{as_of}.{caveat}",
        )

    return ChecklistItem(
        key="captain",
        title="Captain",
        state="attention",
        detail=(
            f"{len(captains)} players carry the armband{as_of}: "
            f"{', '.join(_names(captains))}. Only one can.{caveat}"
        ),
        players=_names(captains),
    )


def flagged_xi_item(xi: list[Player]) -> ChecklistItem:
    if not xi:
        return ChecklistItem(
            key="flagged_in_xi",
            title="Flagged players in the XI",
            state="unknown",
            detail="No XI has been ingested, so nothing can be checked. Run fpl-ingest.",
        )

    if all(player.status is None for player in xi):
        return ChecklistItem(
            key="flagged_in_xi",
            title="Flagged players in the XI",
            state="unknown",
            detail=(
                "No injury statuses have been ingested for these players, so a "
                "flagged player would not show here."
            ),
        )

    flagged = [player for player in xi if player.status in FLAGGED_STATUSES]
    # A player with no status is not a player who is fine. Counted separately
    # so a partially-ingested XI cannot come back as a clean bill of health --
    # "all available" over a squad we only half checked is the single most
    # misleading thing this item could say.
    unchecked = [player for player in xi if player.status is None]

    if flagged:
        gap = (
            f" {len(unchecked)} other {_plural(len(unchecked), 'player', 'players')} "
            f"({', '.join(_names(unchecked))}) {_plural(len(unchecked), 'has', 'have')} "
            "no ingested status, so there may be more."
            if unchecked
            else ""
        )
        return ChecklistItem(
            key="flagged_in_xi",
            title="Flagged players in the XI",
            state="attention",
            detail=(
                f"{len(flagged)} {_plural(len(flagged), 'player', 'players')} in the XI "
                f"{_plural(len(flagged), 'is', 'are')} flagged: {_listing(flagged)}.{gap}"
            ),
            players=_names(flagged),
        )

    if unchecked:
        return ChecklistItem(
            key="flagged_in_xi",
            title="Flagged players in the XI",
            state="unknown",
            detail=(
                f"{len(xi) - len(unchecked)} of {len(xi)} players in the XI are listed "
                f"as available, but {', '.join(_names(unchecked))} "
                f"{_plural(len(unchecked), 'has', 'have')} no ingested status, so a "
                "flag on them would not show here."
            ),
            players=_names(unchecked),
        )

    return ChecklistItem(
        key="flagged_in_xi",
        title="Flagged players in the XI",
        state="ok",
        detail=f"All {len(xi)} players in the XI are listed as available.",
    )


def bench_order_item(bench: list[Player]) -> ChecklistItem:
    """Autosubs come on in bench order, so a flagged player sitting ahead of a
    fit one is a wasted substitution -- a checkable fact, not a preference.

    Scoped to outfield subs by POSITION rather than by slot number: a
    goalkeeper only ever replaces a goalkeeper, so comparing them against the
    outfield order would invent a problem that FPL's own rules make
    impossible.
    """
    if not bench:
        return ChecklistItem(
            key="bench_order",
            title="Bench order",
            state="unknown",
            detail="No bench has been ingested, so its order cannot be checked.",
        )

    outfield = [player for player in bench if player.position != "GK"]
    if len(outfield) < 2:
        return ChecklistItem(
            key="bench_order",
            title="Bench order",
            state="unknown",
            detail=(
                "Fewer than two outfield substitutes have been ingested, so there is "
                "no order to check."
            ),
        )

    order = " then ".join(player.name for player in outfield)
    flagged = [player for player in outfield if player.status in FLAGGED_STATUSES]
    if not flagged:
        return ChecklistItem(
            key="bench_order",
            title="Bench order",
            state="ok",
            detail=f"Substitutes come on in this order: {order}. All are available.",
        )

    # Only an ahead-of-a-fit-player flag is worth raising. A flagged player in
    # the LAST bench slot is already where a flagged player belongs.
    last_fit = max(
        (i for i, player in enumerate(outfield) if player.status not in FLAGGED_STATUSES),
        default=-1,
    )
    blocking = [
        player
        for i, player in enumerate(outfield)
        if i < last_fit and player.status in FLAGGED_STATUSES
    ]

    if not blocking:
        return ChecklistItem(
            key="bench_order",
            title="Bench order",
            state="ok",
            detail=(
                f"Substitutes come on in this order: {order}. The flagged "
                f"{_plural(len(flagged), 'one', 'ones')} ({', '.join(_names(flagged))}) "
                "already sit last."
            ),
            players=_names(flagged),
        )

    return ChecklistItem(
        key="bench_order",
        title="Bench order",
        state="attention",
        detail=(
            f"Substitutes come on in this order: {order}. "
            f"{', '.join(_names(blocking))} {_plural(len(blocking), 'is', 'are')} "
            f"flagged ({_listing(blocking)}) but "
            f"{_plural(len(blocking), 'sits', 'sit')} ahead of an available player, so "
            "an autosub would reach them first."
        ),
        players=_names(blocking),
    )


def bank_item(team: Team) -> ChecklistItem:
    finances = team.finances
    if finances is None or finances.bank is None:
        return ChecklistItem(
            key="bank",
            title="Bank",
            state="unknown",
            detail="No bank balance has been ingested. Run fpl-ingest.",
        )

    bank = finances.bank
    # Stated on every branch, not only when it bites: what a sale actually
    # raises is the number a transfer decision turns on, and "GBP 1.4m banked"
    # reads as a complete budget unless the missing half is named.
    caveat = (
        ""
        if finances.selling_prices_exact
        else (
            " FPL does not publish per-player selling prices without an authenticated "
            "session, so what selling anyone raises on top of this is an upper bound, "
            "not a figure."
        )
    )

    if bank >= IDLE_BANK:
        return ChecklistItem(
            key="bank",
            title="Bank",
            state="attention",
            detail=(
                f"GBP {bank}m is sitting in the bank -- enough to fund an upgrade on "
                f"its own.{caveat}"
            ),
        )

    return ChecklistItem(
        key="bank",
        title="Bank",
        state="ok",
        detail=f"GBP {bank}m in the bank.{caveat}",
    )


def blank_fixture_item(
    conn: Connection, xi: list[Player], team_id: int, target: int | None
) -> ChecklistItem:
    """Whether anyone in the XI has no fixture at all in the target gameweek.

    A blank is the failure this screen exists to catch and the AI card above
    it cannot: a player who is fit, in form, correctly captained, and playing
    nobody. Resolved through issue 21's `get_fixture_run`, which distinguishes
    a genuine blank from a fixture FPL has not scheduled yet -- a distinction
    `next_fixture_by_club` collapses.
    """
    if not xi:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="unknown",
            detail="No XI has been ingested, so fixtures cannot be checked.",
        )
    if target is None:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="unknown",
            detail=(
                "No gameweek is known yet, so there is nothing to check fixtures "
                "against. Run fpl-ingest."
            ),
        )

    clubs = sorted({player.club for player in xi})
    run = get_fixture_run(conn, clubs, team_id, weeks=1, from_gameweek=target)

    if run.from_gameweek is None:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="unknown",
            detail=(
                "No fixtures have been ingested, so a blank gameweek would not show "
                "here. Run fpl-ingest."
            ),
        )

    # Three outcomes, not two. A club with no fixture in the gameweek AND a
    # fixture FPL has not scheduled yet is not blank -- that fixture may still
    # land here. Ticket 21 keeps the two apart precisely so this screen does
    # not collapse them, and calling an unscheduled club "blank" would be the
    # same confidently-wrong claim in a new place.
    blank_clubs = {
        club.club
        for club in run.clubs
        if target in club.blanks and club.unscheduled_fixtures == 0
    }
    pending_clubs = {
        club.club
        for club in run.clubs
        if target in club.blanks and club.unscheduled_fixtures > 0
    }
    # A club whose fixtures could not be resolved at all is not a blank
    # either -- it is a club we know nothing about.
    unresolved = set(run.unknown_clubs)

    blanked = [player for player in xi if player.club in blank_clubs]
    pending = [player for player in xi if player.club in pending_clubs]
    unknown_players = [player for player in xi if player.club in unresolved]

    if blanked:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="attention",
            detail=(
                f"{len(blanked)} {_plural(len(blanked), 'player', 'players')} in the "
                f"XI {_plural(len(blanked), 'has', 'have')} no fixture in GW{target} "
                f"-- a blank: {', '.join(f'{p.name} ({p.club})' for p in blanked)}. "
                "They score nothing that gameweek."
            ),
            players=_names(blanked),
        )

    if pending:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="unknown",
            detail=(
                f"{', '.join(sorted(pending_clubs))} {_plural(len(pending_clubs), 'has', 'have')} "
                f"no fixture listed in GW{target}, but also "
                f"{_plural(len(pending_clubs), 'has', 'have')} a fixture FPL has not "
                f"scheduled yet -- so whether {', '.join(_names(pending))} "
                f"{_plural(len(pending), 'plays', 'play')} is not settled. That is "
                "unscheduled, not a blank."
            ),
            players=_names(pending),
        )

    if unknown_players:
        return ChecklistItem(
            key="blank_fixture",
            title="Fixtures for the XI",
            state="unknown",
            detail=(
                f"No fixtures could be found for {', '.join(sorted(unresolved))}, so "
                f"whether {', '.join(_names(unknown_players))} "
                f"{_plural(len(unknown_players), 'plays', 'play')} in GW{target} is "
                "not known."
            ),
            players=_names(unknown_players),
        )

    return ChecklistItem(
        key="blank_fixture",
        title="Fixtures for the XI",
        state="ok",
        detail=f"Every player in the XI has a fixture in GW{target}.",
    )


# ---------------------------------------------------------------------------
# The screen


def build_checklist(
    conn: Connection, team_id: int, now: datetime | None = None
) -> DeadlineChecklist:
    """The whole deadline screen, from the database alone.

    Built on `get_my_team` rather than its own squad query so the checklist
    and the squad list on the same screen cannot disagree about who is in the
    XI or who has the armband.
    """
    team = get_my_team(conn, team_id)
    countdown = deadline_countdown(conn, team_id, now)

    # The gameweek every item is about. The next unpassed deadline is the one
    # the user can still act for; planning_gameweek is the fallback for when
    # no calendar has been ingested at all.
    target = countdown.gameweek if countdown.gameweek is not None else countdown.planning_gameweek

    # Sorted here rather than relied on from the query. get_my_team does order
    # by squad_position, but bench_order_item's whole answer is the ORDER, and
    # a check that silently depends on somebody else's ORDER BY is one refactor
    # away from reporting a wrong autosub order with total confidence.
    ranked = sorted(
        (player for player in team.players if player.squad_position is not None),
        key=lambda player: player.squad_position,
    )
    xi = [player for player in ranked if player.squad_position <= XI_SIZE]
    bench = [player for player in ranked if player.squad_position > XI_SIZE]

    items = [
        captain_item(xi, team.squad_gameweek, target),
        flagged_xi_item(xi),
        blank_fixture_item(conn, xi, team_id, target),
        bench_order_item(bench),
        bank_item(team),
    ]

    price_risk = _price_risk(conn, team)

    return DeadlineChecklist(
        target_gameweek=target,
        squad_gameweek=team.squad_gameweek,
        countdown=countdown,
        items=items,
        price_risk=price_risk,
        attention_count=sum(1 for item in items if item.state == "attention"),
        unknown_count=sum(1 for item in items if item.state == "unknown"),
    )


def _price_risk(conn: Connection, team: Team) -> PriceChangePredictions:
    """Price movement on the user's own players only.

    `Player.id` is already COALESCE(fpl_element_id, id) out of get_my_team, so
    a manually seeded row carries a serial id that matches no fpl_players row
    and simply drops out -- which is correct: there are no transfer counts
    behind it to predict from.
    """
    return price_changes_for(conn, [player.id for player in team.players])
