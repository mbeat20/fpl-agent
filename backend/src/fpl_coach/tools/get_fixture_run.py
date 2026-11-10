"""Issue 21: a club's fixture RUN, not its next fixture.

`fixtures.next_fixture_by_club` returns exactly one row per club --
`SELECT DISTINCT ON (club) ... WHERE kickoff_time > now()`. Every consumer of
it (`get_injury_news`, `recommend_transfers`, and therefore every
Recommendation and every TransferPlan step) sees the football world one
gameweek deep, while `fpl_rules.py` instructs the model that

    A move that is neutral now but sets up a good run of fixtures beats a
    move that wins this week and forces another next week.

It could not see a run. Worse, `DISTINCT ON` silently keeps one row per club,
so the two structural facts chips exist for are erased: a **blank** (no
fixture that gameweek) and a **double** (two). A plan that plays Bench Boost
into a blank is the confidently-wrong advice this tool prevents.

Three things this is careful about:

- **Home and away survive.** The existing query's `UNION ALL` flattens them
  into one "opponent" column. Home advantage is part of how hard a fixture is,
  and FPL rates the two sides of the same fixture differently -- taking the
  wrong side's FDR inverts the judgement.
- **A blank is not missing data.** `fixtures.gameweek` is nullable (an
  unscheduled fixture) and a club genuinely having no fixture are different
  facts. `next_fixture_by_club` returns None for both; this does not.
- **Individual fixtures come back with the aggregate.** An average alone hides
  a run that is three easy games and then a wall, which is precisely the shape
  a transfer plan should be built around.

`next_fixture_by_club` is untouched. This is an additional capability, not a
refactor of tickets 07 and 11.
"""

from psycopg import Connection

from fpl_coach.gameweek import planning_gameweek as resolve_planning_gameweek
from fpl_coach.models import ClubFixtureRun, FixtureRun, GameweekFixtures, RunFixture
from fpl_coach.reasoning.base import ToolSpec

# FPL's own convention. Every fixture-ticker in the game shows the next five,
# and it is about as far as a transfer plan can see before injuries and price
# moves make the detail worthless.
DEFAULT_WEEKS = 5
# Beyond this a "run" is a season projection dressed up as a fixture list.
MAX_WEEKS = 12

# One row per club per fixture, home and away kept apart. Emitting both sides
# of every fixture and filtering by club in the WHERE is what lets a single
# query answer for many clubs at once -- the ticket is explicit that looping
# one club at a time would burn the reasoning layer's turn budget.
_RUN_SQL = """
WITH sides AS (
    SELECT home_team AS club, away_team AS opponent, TRUE AS is_home,
           home_difficulty AS difficulty, gameweek, kickoff_time
    FROM fixtures
    UNION ALL
    SELECT away_team AS club, home_team AS opponent, FALSE AS is_home,
           away_difficulty AS difficulty, gameweek, kickoff_time
    FROM fixtures
)
SELECT club, opponent, is_home, difficulty, gameweek, kickoff_time
FROM sides
WHERE club = ANY(%(clubs)s)
  AND gameweek BETWEEN %(from_gameweek)s AND %(to_gameweek)s
ORDER BY club, gameweek, kickoff_time NULLS LAST
"""

# Fixtures FPL has not yet given a gameweek. Counted per club so "not
# scheduled yet" never masquerades as "no fixture".
_UNSCHEDULED_SQL = """
SELECT club, count(*) AS n FROM (
    SELECT home_team AS club FROM fixtures WHERE gameweek IS NULL
    UNION ALL
    SELECT away_team AS club FROM fixtures WHERE gameweek IS NULL
) sides
WHERE club = ANY(%s)
GROUP BY club
"""

# Resolve however the user or the model referred to a club. Short name is the
# join key everywhere in this database ('ARS'), but a question says "Arsenal",
# so both are accepted. Falls back to the distinct club names actually present
# in `fixtures` when the clubs table has not been ingested yet, so the tool
# works before the next fpl-ingest rather than returning an empty run.
_CLUB_LOOKUP_SQL = """
SELECT short_name, name FROM clubs
WHERE short_name ILIKE %(needle)s OR name ILIKE %(needle)s
ORDER BY (short_name ILIKE %(needle)s) DESC, short_name
LIMIT 1
"""

_FIXTURE_CLUB_LOOKUP_SQL = """
SELECT DISTINCT club FROM (
    SELECT home_team AS club FROM fixtures
    UNION SELECT away_team AS club FROM fixtures
) sides
WHERE club ILIKE %s
ORDER BY club
LIMIT 1
"""


def _resolve_club(conn: Connection, requested: str) -> tuple[str, str | None] | None:
    """(short_name, full_name) for however the club was named, or None."""
    needle = requested.strip()
    if not needle:
        return None
    with conn.cursor() as cur:
        cur.execute(_CLUB_LOOKUP_SQL, {"needle": needle})
        row = cur.fetchone()
        if row is not None:
            return row["short_name"], row["name"]
        cur.execute(_FIXTURE_CLUB_LOOKUP_SQL, (needle,))
        row = cur.fetchone()
    return (row["club"], None) if row is not None else None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _build_run(
    club: str,
    name: str | None,
    rows: list[dict],
    from_gameweek: int,
    to_gameweek: int,
    unscheduled: int,
) -> ClubFixtureRun:
    by_gameweek: dict[int, list[RunFixture]] = {
        gameweek: [] for gameweek in range(from_gameweek, to_gameweek + 1)
    }
    for row in rows:
        by_gameweek[row["gameweek"]].append(
            RunFixture(
                gameweek=row["gameweek"],
                opponent=row["opponent"],
                opponent_name=row.get("opponent_name"),
                is_home=row["is_home"],
                difficulty=row["difficulty"],
                kickoff_time=_iso(row["kickoff_time"]),
            )
        )

    gameweeks = [
        GameweekFixtures(
            gameweek=gameweek,
            fixtures=fixtures,
            count=len(fixtures),
            is_blank=len(fixtures) == 0,
            is_double=len(fixtures) >= 2,
        )
        for gameweek, fixtures in sorted(by_gameweek.items())
    ]

    difficulties = [
        fixture.difficulty
        for week in gameweeks
        for fixture in week.fixtures
        if fixture.difficulty is not None
    ]
    fixtures_count = sum(week.count for week in gameweeks)
    complete = len(difficulties) == fixtures_count

    blanks = [week.gameweek for week in gameweeks if week.is_blank]
    doubles = [week.gameweek for week in gameweeks if week.is_double]

    notes = []
    if blanks:
        notes.append(
            f"{club} has no fixture in GW{', GW'.join(str(gw) for gw in blanks)} -- a "
            "blank. A player of theirs scores nothing that gameweek, and a Bench Boost "
            "or Triple Captain aimed at it is wasted."
        )
    if doubles:
        notes.append(
            f"{club} plays twice in GW{', GW'.join(str(gw) for gw in doubles)} -- a "
            "double gameweek."
        )
    if unscheduled:
        notes.append(
            f"{unscheduled} further {club} fixture(s) have no gameweek assigned yet. "
            "That is unscheduled, NOT a blank -- they may still land inside this "
            "horizon."
        )
    if not complete:
        notes.append(
            "Some fixtures in this run carry no difficulty rating, so the aggregates "
            "below cover only the rated ones. Treat the missing ones as unknown, not "
            "as easy."
        )

    return ClubFixtureRun(
        club=club,
        name=name,
        gameweeks=gameweeks,
        fixtures_count=fixtures_count,
        blanks=blanks,
        doubles=doubles,
        total_difficulty=sum(difficulties) if difficulties else None,
        # Per fixture, never per gameweek -- see ClubFixtureRun's docstring.
        average_difficulty=(
            round(sum(difficulties) / len(difficulties), 2) if difficulties else None
        ),
        difficulty_complete=complete,
        unscheduled_fixtures=unscheduled,
        note=" ".join(notes) or None,
    )


def get_fixture_run(
    conn: Connection,
    clubs: list[str],
    team_id: int,
    weeks: int = DEFAULT_WEEKS,
    from_gameweek: int | None = None,
) -> FixtureRun:
    """The next `weeks` gameweeks for each named club.

    The horizon starts at the planning gameweek, not at "the next fixture in
    the table". Those differ in exactly the window where planning happens --
    FPL leaves is_current on a finished gameweek until the next kicks off --
    and starting the run on a gameweek whose deadline has passed would offer
    a fixture nobody can act on as the first step of a plan.
    """
    # `weeks or DEFAULT_WEEKS` would be wrong: 0 is falsy, so an explicit
    # weeks=0 from the model would silently become a five-gameweek horizon
    # rather than being clamped to the one gameweek it asked for.
    weeks = DEFAULT_WEEKS if weeks is None else max(1, min(int(weeks), MAX_WEEKS))
    start = from_gameweek if from_gameweek is not None else resolve_planning_gameweek(
        conn, team_id
    )
    if start is None:
        return FixtureRun(
            weeks=weeks,
            note=(
                "No gameweek is known yet -- neither ingestion nor fixtures have "
                "resolved one. Run fpl-ingest before asking about fixture runs."
            ),
        )
    end = start + weeks - 1

    resolved: list[tuple[str, str | None]] = []
    unknown: list[str] = []
    for requested in clubs:
        match = _resolve_club(conn, requested)
        if match is None:
            unknown.append(requested)
        elif match[0] not in {short for short, _ in resolved}:
            resolved.append(match)

    if not resolved:
        return FixtureRun(
            from_gameweek=start,
            to_gameweek=end,
            weeks=weeks,
            unknown_clubs=unknown,
            note=(
                "None of the names given matched a club. Use the club as it appears on "
                "a player (get_my_team and get_player both return it), e.g. 'ARS'."
            ),
        )

    short_names = [short for short, _ in resolved]
    with conn.cursor() as cur:
        cur.execute(
            _RUN_SQL, {"clubs": short_names, "from_gameweek": start, "to_gameweek": end}
        )
        rows = cur.fetchall()
        cur.execute(_UNSCHEDULED_SQL, (short_names,))
        unscheduled = {row["club"]: row["n"] for row in cur.fetchall()}
        cur.execute("SELECT short_name, name FROM clubs")
        full_names = {row["short_name"]: row["name"] for row in cur.fetchall()}

    for row in rows:
        row["opponent_name"] = full_names.get(row["opponent"])

    rows_by_club: dict[str, list[dict]] = {short: [] for short in short_names}
    for row in rows:
        rows_by_club[row["club"]].append(row)

    return FixtureRun(
        from_gameweek=start,
        to_gameweek=end,
        weeks=weeks,
        clubs=[
            _build_run(
                short, name, rows_by_club[short], start, end, unscheduled.get(short, 0)
            )
            for short, name in resolved
        ],
        unknown_clubs=unknown,
    )


def make_get_fixture_run_tool(conn: Connection, team_id: int) -> ToolSpec:
    return ToolSpec(
        name="get_fixture_run",
        description=(
            "Returns each named club's fixtures over the next few gameweeks -- every "
            "fixture with its opponent, home or away, and FPL's own difficulty rating "
            "(FDR, 1 easy to 5 hard) for that club in that fixture. Pass several clubs "
            "at once to compare runs; do not call it once per club. "
            "Use it for any question that looks past this gameweek: a transfer that "
            "sets up a run, a captain two weeks out, when to play a chip. "
            "Read `blanks` and `doubles` first. A BLANK is a gameweek where the club "
            "has no fixture at all -- their players score nothing, and a Bench Boost or "
            "Triple Captain aimed at it is wasted. A DOUBLE is two fixtures in one "
            "gameweek and is what chips are worth saving for. Both are stated "
            "explicitly; do not try to infer them by counting. "
            "`average_difficulty` is per fixture, not per gameweek, so read it "
            "alongside `fixtures_count` -- a club with an easy average and a blank in "
            "the middle is not the asset the average alone suggests. "
            "`unscheduled_fixtures` means FPL has not assigned a gameweek yet; that is "
            "NOT a blank. Cite the specific gameweek and opponent when you use this, "
            "not generic fixture prose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "clubs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "club short names as they appear on a player (e.g. 'ARS', "
                        "'MCI'); full names such as 'Arsenal' also work"
                    ),
                },
                "weeks": {
                    "type": "integer",
                    "description": (
                        f"how many gameweeks ahead to look, default {DEFAULT_WEEKS}, "
                        f"maximum {MAX_WEEKS}"
                    ),
                },
            },
            "required": ["clubs"],
        },
        handler=lambda clubs, weeks=DEFAULT_WEEKS: get_fixture_run(
            conn, clubs, team_id, weeks
        ),
    )
