"""Issue 25: a Roadmap is a plan the user is FOLLOWING.

CONTEXT.md says a Horizon is "resolved live from fixtures/data each time.
Never persisted", and that is still right -- a Horizon is a property of a
question. A Roadmap is a different thing, and the difference is that the user
can be shown to have drifted from it. Ticket 12 built `TransferPlan` and it
works, but the plan evaporates the moment the response renders: it cannot be
revisited, cannot be diffed against what actually happened, and cannot be
caught rotting when the fixture list changes underneath it.

Three responsibilities live here, in order of how load-bearing they are.

**Validation.** `plan_rules.walk_plan` holds the arithmetic; this module
resolves a `TransferPlan`'s player names into real players, prices and clubs
and feeds it. It is wired into the finalisation gate, so a plan whose steps
do not add up is refused and revised rather than shown to the user --
`recommend_transfers` for a sequence.

**Versioning.** Every stored roadmap is a new version and nothing is ever
overwritten. `team_suggestions` overwrites because a cache has no use for its
own history; here the history IS the value. preferences.md asks to be able to
say why a pick changed -- genuine reassessment versus bandwagon influence --
and that question is unanswerable against a row that has been replaced in
place.

**The check-in.** What changed since the roadmap was made. Note that
`fixtures` is wholesale replaced by every run_ingestion (ticket 21), so a
stored plan rots silently: a gameweek can become a blank with no record that
it ever was not. That is why a fixture snapshot is stored alongside the plan
-- without it the check-in can report what is true today but not what
CHANGED, and only the second is news.

The check-in reports and stops, like ticket 23's checklist. It names the steps
that no longer hold and does not write a replacement: a revision is a
reasoning call the user chooses to make, not something the app does to their
plan behind their back.
"""

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import (
    FixtureChange,
    PlanValidation,
    Roadmap,
    RoadmapChange,
    RoadmapCheckIn,
    StepCost,
    StepViolation,
    TransferPlan,
)
from fpl_coach.plan_rules import PlannedStep, is_legal, summarise, walk_plan
from fpl_coach.squad_state import sellable_value
from fpl_coach.tools.get_fixture_run import MAX_WEEKS, get_fixture_run
from fpl_coach.tools.get_player import find_player
from fpl_coach.tools.validate_squad_move import free_transfers

_FLAGGED_STATUSES = {"d", "i", "s", "u"}
_STATUS_WORDS = {
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
}

_SQUAD_SQL = "SELECT name, position, club, price, status FROM players WHERE team_id = %s"


# ---------------------------------------------------------------------------
# Resolving a plan into something checkable


def _squad(conn: Connection, team_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(_SQUAD_SQL, (team_id,))
        return [
            {
                "name": row["name"],
                "position": row["position"],
                "club": row["club"],
                "price": float(row["price"]),
                "status": row["status"],
            }
            for row in cur.fetchall()
        ]


def _match(squad: list[dict], name: str | None) -> dict | None:
    if not name:
        return None
    needle = name.strip().casefold()
    for player in squad:
        if player["name"].casefold() == needle:
            return player
    for player in squad:
        if needle and needle in player["name"].casefold():
            return player
    return None


def _resolve_incoming(conn: Connection, name: str | None) -> dict | None:
    """A named player as a full record, or None when nothing matches.

    Resolution failure is NOT reported here as a violation. The step is left
    with no incoming player and `_transfer_violations` raises
    `missing_player_in` against it, so an unknown name and an omitted name
    produce the same shaped refusal rather than two paths that can drift.
    """
    if not name:
        return None
    found = find_player(conn, name)
    if found is None:
        return None
    return {
        "name": found.name,
        "position": found.position,
        "club": found.club,
        "price": float(found.price),
    }


def _planned_steps(conn: Connection, plan: TransferPlan, squad: list[dict]) -> list[PlannedStep]:
    """Plan steps resolved to real players, indexed AS SUBMITTED.

    The outgoing player is looked up in the squad the user actually holds
    rather than in the whole pool: `walk_plan` re-checks membership against
    the simulated squad anyway, and matching against the pool would resolve a
    name the user has never owned into a plausible-looking record and hide the
    real problem behind an affordability error.
    """
    steps = []
    for index, step in enumerate(plan.steps):
        incoming = (
            _resolve_incoming(conn, step.pick.player_name) if step.pick is not None else None
        )
        outgoing = _match(squad, step.player_out.player_name if step.player_out else None)
        # Fall back to the plan's own name so a sold-then-resold player still
        # reports against the name the model wrote, not against nothing.
        if outgoing is None and step.player_out is not None:
            outgoing = {
                "name": step.player_out.player_name,
                "position": "",
                "club": "",
                "price": 0.0,
            }
        steps.append(
            PlannedStep(
                index=index,
                gameweek=step.gameweek,
                decision=step.decision,
                player_in=incoming,
                player_out=outgoing,
            )
        )
    return steps


def _blanks_for(
    conn: Connection, steps: list[PlannedStep], team_id: int, start: int | None
) -> tuple[dict[str, set[int]], bool]:
    """(club -> gameweeks with no fixture, whether the whole span was covered).

    `get_fixture_run` clamps at MAX_WEEKS, so a season-long plan reaches past
    what can be checked. The second element says so rather than letting a
    partially-checked plan read as a fully-checked one.
    """
    clubs = sorted(
        {step.player_in["club"] for step in steps if step.player_in and step.player_in["club"]}
    )
    gameweeks = [step.gameweek for step in steps]
    if not clubs or not gameweeks:
        return {}, True

    first = start if start is not None else min(gameweeks)
    span = max(gameweeks) - first + 1
    covered = span <= MAX_WEEKS
    run = get_fixture_run(conn, clubs, team_id, weeks=min(max(span, 1), MAX_WEEKS), from_gameweek=first)
    return {club.club: set(club.blanks) for club in run.clubs}, covered


def fixture_snapshot(conn: Connection, plan: TransferPlan, team_id: int) -> dict:
    """{club: {gameweek: fixture_count}} for everything the plan touches.

    Stored with the roadmap so the check-in can say a gameweek BECAME a blank.
    Without it a blank looks the same whether it was always there or appeared
    last week, and only the second is worth telling the user about.
    """
    squad = _squad(conn, team_id)
    steps = _planned_steps(conn, plan, squad)
    clubs = sorted(
        {step.player_in["club"] for step in steps if step.player_in and step.player_in["club"]}
    )
    gameweeks = [step.gameweek for step in steps]
    if not clubs or not gameweeks:
        return {}

    first, last = min(gameweeks), max(gameweeks)
    span = min(max(last - first + 1, 1), MAX_WEEKS)
    run = get_fixture_run(conn, clubs, team_id, weeks=span, from_gameweek=first)
    return {
        club.club: {str(week.gameweek): week.count for week in club.gameweeks}
        for club in run.clubs
    }


# ---------------------------------------------------------------------------
# Validation


def validate_plan(conn: Connection, plan: TransferPlan, team_id: int) -> PlanValidation:
    """The deterministic verdict on a whole plan, from stored state alone."""
    squad = _squad(conn, team_id)
    finances = sellable_value(conn, team_id)
    start = resolve_planning_gameweek(conn, team_id)
    free = free_transfers(conn, team_id)

    steps = _planned_steps(conn, plan, squad)
    blanks, fully_covered = _blanks_for(conn, steps, team_id, start)

    raw_violations, costs, totals = walk_plan(
        steps,
        squad=squad,
        bank=finances.bank,
        free_at_start=free,
        start_gameweek=start,
        blanks=blanks,
        selling_prices_exact=finances.selling_prices_exact,
    )

    legal = is_legal(raw_violations)
    notes = []
    if finances.bank is None:
        notes.append(
            "No bank figure has been ingested, so no step's affordability was checked. "
            "Do not present this plan as affordable."
        )
    if free is None:
        notes.append(
            "No transfer history has been ingested, so free transfers and hits could "
            "not be counted."
        )
    if not finances.selling_prices_exact and finances.bank is not None:
        notes.append(
            "Selling prices are not exact (FPL does not publish them without an "
            "authenticated session), so every budget figure here is an UPPER bound and "
            "a step inside that gap may not actually be affordable."
        )
    if not fully_covered:
        notes.append(
            f"This plan reaches further than {MAX_WEEKS} gameweeks, so blank-gameweek "
            "checks cover only the first part of it."
        )
    if len(squad) != 15:
        notes.append(
            f"The ingested squad holds {len(squad)} players, not 15, so the composition "
            "rule was not applied."
        )

    return PlanValidation(
        legal=legal,
        violations=[
            StepViolation(
                step_index=step.index,
                gameweek=step.gameweek,
                rule=violation.rule,
                message=violation.message,
            )
            for step, violation in raw_violations
        ],
        steps=[StepCost(**cost) for cost in costs],
        total_hit_cost=totals["total_hit_cost"],
        transfers_planned=totals["transfers_planned"],
        affordability_exact=finances.selling_prices_exact,
        summary=summarise(legal, raw_violations, totals),
        note=" ".join(notes) or None,
    )


def plan_problems(conn: Connection, plan: TransferPlan, team_id: int) -> list[str]:
    """Violation messages for the finalisation gate, empty when the plan holds.

    Every violation counts, not only the illegal ones: a step aimed at a blank
    is legal and still must not reach the user as a plan, which is the whole
    reason ticket 21 built blank detection.
    """
    validation = validate_plan(conn, plan, team_id)
    return [
        f"Step {v.step_index + 1} (GW{v.gameweek}): {v.message}" for v in validation.violations
    ]


# ---------------------------------------------------------------------------
# Persistence


def _row_to_roadmap(row: dict, planning: int | None) -> Roadmap | None:
    try:
        plan = TransferPlan(**row["plan"])
    except ValidationError:
        # A version stored under an older TransferPlan shape. Unlike
        # team_suggestions -- a cache, where a miss just regenerates -- this is
        # history, so it is skipped rather than deleted or coerced. A version
        # that cannot be read honestly is better absent than half-read.
        return None
    return Roadmap(
        id=row["id"],
        team_id=row["team_id"],
        version=row["version"],
        status=_status(row, plan, planning),
        plan=plan,
        from_gameweek=row["from_gameweek"],
        prompt=row["prompt"],
        created_at=row["created_at"].isoformat(),
        superseded_at=row["superseded_at"].isoformat() if row["superseded_at"] else None,
        supersede_reason=row["supersede_reason"],
    )


def _status(row: dict, plan: TransferPlan, planning: int | None) -> str:
    """live / completed / superseded.

    `completed` exists so a plan that has simply run its course does not read
    as a plan the user is still meant to be following. The ticket asks for a
    roadmap to be considered dead rather than left looking authoritative for a
    month; every step being in the past is the honest definition of that, and
    it needs no arbitrary age cutoff.
    """
    if row["superseded_at"] is not None:
        return "superseded"
    if planning is not None and plan.steps and all(s.gameweek < planning for s in plan.steps):
        return "completed"
    return "live"


_SELECT = (
    "SELECT id, team_id, version, plan, fixture_snapshot, from_gameweek, prompt, "
    "created_at, superseded_at, supersede_reason FROM roadmaps"
)


def get_live_roadmap(conn: Connection, team_id: int) -> Roadmap | None:
    with conn.cursor() as cur:
        cur.execute(
            _SELECT + " WHERE team_id = %s AND superseded_at IS NULL", (team_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_roadmap(row, resolve_planning_gameweek(conn, team_id))


def get_roadmap_versions(conn: Connection, team_id: int) -> list[Roadmap]:
    """Every version, newest first. Superseded ones stay readable -- that is
    the point of versioning rather than overwriting."""
    planning = resolve_planning_gameweek(conn, team_id)
    with conn.cursor() as cur:
        cur.execute(_SELECT + " WHERE team_id = %s ORDER BY version DESC", (team_id,))
        rows = cur.fetchall()
    return [r for r in (_row_to_roadmap(row, planning) for row in rows) if r is not None]


def save_roadmap(
    conn: Connection,
    team_id: int,
    plan: TransferPlan,
    prompt: str | None = None,
    reason: str | None = None,
) -> Roadmap:
    """Store a plan as the new live roadmap, superseding any previous one.

    The previous version is marked superseded rather than deleted, and the
    reason is written onto the OLD row -- so reading the history explains
    itself without having to infer why each version stopped being current.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    snapshot = fixture_snapshot(conn, plan, team_id)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM roadmaps WHERE team_id = %s",
            (team_id,),
        )
        version = cur.fetchone()["v"] + 1

        cur.execute(
            "UPDATE roadmaps SET superseded_at = now(), supersede_reason = %s "
            "WHERE team_id = %s AND superseded_at IS NULL",
            (
                reason
                or f"Replaced by version {version}"
                + (f", generated from: {prompt}" if prompt else "."),
                team_id,
            ),
        )
        cur.execute(
            "INSERT INTO roadmaps (team_id, version, plan, fixture_snapshot, "
            "from_gameweek, prompt) VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, team_id, version, plan, fixture_snapshot, from_gameweek, "
            "prompt, created_at, superseded_at, supersede_reason",
            (
                team_id,
                version,
                Jsonb(plan.model_dump(mode="json")),
                Jsonb(snapshot),
                planning,
                prompt,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return _row_to_roadmap(row, planning)


# ---------------------------------------------------------------------------
# The check-in


def _fixture_changes(stored: dict, current: dict) -> list[FixtureChange]:
    """What the fixture list did to this plan while it sat in the database."""
    changes: list[FixtureChange] = []
    for club, weeks in sorted(stored.items()):
        for gameweek, was in sorted(weeks.items(), key=lambda kv: int(kv[0])):
            now = current.get(club, {}).get(gameweek)
            if now is None or now == was:
                continue
            week = int(gameweek)
            if now == 0:
                kind, message = (
                    "became_blank",
                    f"{club} had a fixture in GW{week} when this roadmap was made and "
                    "now has none -- it has become a blank.",
                )
            elif was == 0:
                kind, message = (
                    "fixture_added",
                    f"{club} had no fixture in GW{week} when this roadmap was made and "
                    "now has one.",
                )
            elif now > was:
                kind, message = (
                    "became_double",
                    f"{club} now plays {now} times in GW{week}, up from {was} -- it has "
                    "become a double gameweek.",
                )
            else:
                kind, message = (
                    "fixture_removed",
                    f"{club} now plays {now} time(s) in GW{week}, down from {was}.",
                )
            changes.append(
                FixtureChange(
                    club=club, gameweek=week, was=was, now=now, kind=kind, message=message
                )
            )
    return changes


def _injury_changes(
    plan: TransferPlan, squad: list[dict], remaining: list[int], conn: Connection
) -> list[RoadmapChange]:
    """A remaining step whose player is now flagged.

    Checked against the pool, not only the squad: a step transfers in somebody
    the user does not own yet, and that is exactly the player whose injury
    invalidates the step.
    """
    changes: list[RoadmapChange] = []
    for index in remaining:
        step = plan.steps[index]
        if step.pick is None:
            continue
        held = _match(squad, step.pick.player_name)
        status = held["status"] if held else None
        if status is None:
            found = find_player(conn, step.pick.player_name)
            status = getattr(found, "status", None)
        if status in _FLAGGED_STATUSES:
            changes.append(
                RoadmapChange(
                    step_index=index,
                    gameweek=step.gameweek,
                    kind="injury",
                    player_name=step.pick.player_name,
                    message=(
                        f"{step.pick.player_name} is now {_STATUS_WORDS[status]}. The "
                        f"GW{step.gameweek} step was made before that."
                    ),
                )
            )
    return changes


def check_in(conn: Connection, team_id: int) -> RoadmapCheckIn:
    """What changed since the live roadmap was made.

    Revalidation runs over the REMAINING steps only, starting from the squad,
    bank and prices the user actually holds today. Re-checking elapsed steps
    would report problems with decisions that are already history and bury the
    ones the user can still do something about.
    """
    planning = resolve_planning_gameweek(conn, team_id)
    roadmap = get_live_roadmap(conn, team_id)
    if roadmap is None:
        return RoadmapCheckIn(
            planning_gameweek=planning,
            summary="No roadmap is stored. Ask for a multi-week plan to create one.",
        )

    plan = roadmap.plan
    elapsed = [
        i
        for i, step in enumerate(plan.steps)
        if planning is not None and step.gameweek < planning
    ]
    remaining = [i for i in range(len(plan.steps)) if i not in set(elapsed)]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT fixture_snapshot FROM roadmaps WHERE id = %s", (roadmap.id,)
        )
        stored_snapshot = cur.fetchone()["fixture_snapshot"] or {}

    fixture_changes = _fixture_changes(stored_snapshot, fixture_snapshot(conn, plan, team_id))

    squad = _squad(conn, team_id)
    changes = _injury_changes(plan, squad, remaining, conn)

    for index in elapsed:
        changes.append(
            RoadmapChange(
                step_index=index,
                gameweek=plan.steps[index].gameweek,
                kind="elapsed",
                message=(
                    f"The GW{plan.steps[index].gameweek} step is in the past -- that "
                    "deadline has gone, so it is no longer actionable either way."
                ),
            )
        )

    # A fixture change only matters to a step it actually touches. Reported
    # separately in `fixture_changes` either way -- a double appearing in a
    # gameweek the plan does not use is still worth knowing -- but it only
    # becomes a reason a STEP no longer holds when it lands on that step.
    for change in fixture_changes:
        for index in remaining:
            step = plan.steps[index]
            if step.gameweek == change.gameweek and step.pick is not None:
                changes.append(
                    RoadmapChange(
                        step_index=index,
                        gameweek=step.gameweek,
                        kind="fixture",
                        player_name=step.pick.player_name,
                        message=change.message,
                    )
                )

    revalidation = None
    if remaining:
        forward = TransferPlan(
            horizon=plan.horizon,
            confidence=plan.confidence,
            steps=[plan.steps[i] for i in remaining],
        )
        revalidation = validate_plan(conn, forward, team_id)
        for violation in revalidation.violations:
            # Re-index onto the ORIGINAL plan: the model and the user both
            # refer to steps by where they sit in the roadmap they were shown,
            # not by position in a filtered forward slice.
            original = remaining[violation.step_index]
            changes.append(
                RoadmapChange(
                    step_index=original,
                    gameweek=violation.gameweek,
                    kind="price" if violation.rule == "affordability" else "illegal",
                    message=violation.message,
                )
            )

    still_valid = not any(c.kind != "elapsed" for c in changes)
    return RoadmapCheckIn(
        roadmap=roadmap,
        planning_gameweek=planning,
        elapsed_steps=elapsed,
        remaining_steps=remaining,
        changes=changes,
        fixture_changes=fixture_changes,
        revalidation=revalidation,
        still_valid=still_valid,
        summary=_checkin_summary(roadmap, elapsed, remaining, changes, still_valid),
        note=(
            "This reports what changed; it does not revise the plan. Ask for a new "
            "multi-week plan to replace it -- the current version stays readable "
            "either way."
        ),
    )


def _checkin_summary(roadmap, elapsed, remaining, changes, still_valid) -> str:
    if not remaining:
        return (
            f"Roadmap v{roadmap.version} has run its course -- every step is in the "
            "past. Nothing here is still actionable."
        )
    if still_valid:
        return (
            f"Roadmap v{roadmap.version} still holds. {len(remaining)} step(s) remain "
            f"and nothing has changed under them"
            + (f"; {len(elapsed)} have already passed." if elapsed else ".")
        )
    material = [c for c in changes if c.kind != "elapsed"]
    return (
        f"Roadmap v{roadmap.version} has drifted: {len(material)} of its remaining "
        f"{len(remaining)} step(s) no longer hold. The reasons are listed per step -- "
        "decide whether to revise it or keep it."
    )
