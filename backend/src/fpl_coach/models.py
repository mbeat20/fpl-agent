from typing import Literal

from pydantic import BaseModel


class GameweekMinutes(BaseModel):
    """Issue 20: one gameweek of a player's actual involvement. Season totals
    hide the shape -- 180 minutes is a nailed starter who missed two weeks
    injured, or a substitute playing 20 a week, and those are opposite
    transfer cases."""

    gameweek: int
    minutes: int
    starts: int | None = None
    total_points: int | None = None
    opponent: str | None = None
    was_home: bool | None = None


class Player(BaseModel):
    id: int
    name: str
    position: str
    club: str
    price: float
    # Issue 03: populated by real FPL API ingestion; absent for manually seeded rows.
    ownership: float | None = None
    form: float | None = None
    status: str | None = None
    news: str | None = None
    # Issue 05, ADR-0022: FPL's own season-to-date Opta xG/xA, keyed by
    # player id (no name matching). NULL only if not yet ingested.
    xg: float | None = None
    xa: float | None = None
    # Issue 20: minutes security, the cheap tier -- season totals straight
    # from bootstrap.elements, so they cost no extra API call. `status` only
    # says whether a player is fit; these are what expose a fit player who
    # simply is not being picked.
    minutes: int | None = None
    starts: int | None = None
    # Issue 20: squad shape. 1-11 is the XI, 12-15 the bench in the order
    # they would come on. None for a pool player or a manually seeded row.
    squad_position: int | None = None
    is_captain: bool = False
    is_vice_captain: bool = False
    multiplier: int | None = None
    # Issue 20: derived, not stored -- see rotation_risk() in squad_state.
    rotation_risk: bool = False
    # Issue 20: per-fixture minutes, newest first. Populated by get_player
    # (which fetches on demand for any player the user names); left empty by
    # the bulk squad read, where season totals already answer the question.
    recent_minutes: list[GameweekMinutes] = []


class ChipWindow(BaseModel):
    """Issue 20: one entry from bootstrap's chip catalogue, or one the user
    has already played. FPL issues a fresh set each half of the season, so a
    chip name alone is not unique -- the gameweek window is what identifies
    which of the two a row refers to."""

    name: str
    played_in_gameweek: int | None = None
    available_from_gameweek: int | None = None
    available_until_gameweek: int | None = None
    playable_now: bool = False


class ChipStatus(BaseModel):
    planning_gameweek: int | None = None
    used: list[ChipWindow] = []
    remaining: list[ChipWindow] = []
    playable_now: list[str] = []


class SquadFinances(BaseModel):
    """Issue 20: what the user can spend. `selling_prices_exact` is the
    load-bearing field -- FPL does not publish per-player selling prices
    without an authenticated session, so when it is False a player's listed
    price is only an upper bound on what selling them raises."""

    bank: float | None = None
    # FPL's own "team value" figure, which INCLUDES the bank.
    team_value: float | None = None
    # The 15 players' total selling value: team_value minus bank.
    squad_selling_value: float | None = None
    # What the same 15 players are listed at today. Equal to
    # squad_selling_value only while nobody has changed price since purchase.
    current_price_total: float | None = None
    selling_prices_exact: bool = False
    note: str | None = None


class Team(BaseModel):
    id: int
    name: str
    players: list[Player]
    # Issue 12: lets a Reasoning implementation anchor a multi-week plan's
    # step gameweeks to a real FPL gameweek number. None until run_ingestion
    # has resolved one for this team (same nullability as gameweek_state).
    current_gameweek: int | None = None
    # Issue 20: the gameweek the picks above were actually read from. FPL only
    # exposes picks once a deadline has passed, so this legitimately trails
    # current_gameweek in the pre-deadline window where planning happens --
    # meaning the XI/captain shown are last week's until the next lock.
    squad_gameweek: int | None = None
    finances: SquadFinances | None = None
    # Issue 20: standing context that should change risk appetite --
    # preferences.md already refers to chasing or protecting a rank.
    overall_rank: int | None = None
    gameweek_points: int | None = None
    total_points: int | None = None
    points_on_bench: int | None = None
    event_transfers: int | None = None
    event_transfers_cost: int | None = None
    active_chip: str | None = None


class PlayerComparison(BaseModel):
    players: list[Player]


class RunFixture(BaseModel):
    """Issue 21: one fixture inside a club's run.

    `is_home` is kept because it is thrown away everywhere else: the
    `UNION ALL` in fixtures._NEXT_FIXTURE_SQL flattens home and away into one
    "opponent" column, and home advantage is a real part of how hard a fixture
    is. `difficulty` is FPL's own FDR for THIS club in this fixture -- the
    home side's and away side's ratings differ, and taking the wrong one
    inverts the judgement."""

    gameweek: int
    opponent: str
    opponent_name: str | None = None
    is_home: bool
    difficulty: int | None = None
    kickoff_time: str | None = None


class GameweekFixtures(BaseModel):
    """Issue 21: what a club has on in ONE gameweek -- zero, one, or two-plus
    fixtures.

    `is_blank` and `is_double` are stated rather than left to be counted from
    the list, because both are structural facts a plan turns on (a Bench Boost
    into a blank is the confidently-wrong advice this exists to prevent) and
    a model counting rows will eventually miscount."""

    gameweek: int
    fixtures: list[RunFixture] = []
    count: int = 0
    is_blank: bool = False
    is_double: bool = False


class ClubFixtureRun(BaseModel):
    """Issue 21: one club's run across the horizon.

    On the aggregates: `average_difficulty` is per FIXTURE, not per gameweek.
    Dividing by gameweeks would score a blank as difficulty 0 and make a club
    with no fixture look like a club with an easy one, which is backwards.
    Read `fixtures_count` alongside it -- a 2.0 average over three fixtures in
    five gameweeks is not the same asset as a 2.0 average over five.
    """

    club: str
    name: str | None = None
    gameweeks: list[GameweekFixtures] = []
    fixtures_count: int = 0
    blanks: list[int] = []
    doubles: list[int] = []
    total_difficulty: int | None = None
    average_difficulty: float | None = None
    # False when any fixture in the run has no FDR. The aggregates are then
    # computed over the fixtures that do, and must not be read as complete.
    difficulty_complete: bool = True
    # Fixtures involving this club that FPL has not yet assigned to a
    # gameweek -- a cup replay or a postponement awaiting a slot. Counted
    # separately because "no fixture scheduled yet" and "no fixture" are
    # different facts, and collapsing them is the mistake
    # next_fixture_by_club already makes.
    unscheduled_fixtures: int = 0
    note: str | None = None


class FixtureRun(BaseModel):
    """Issue 21: several clubs' runs over one shared horizon, so the reasoning
    layer can compare them without burning a turn per club."""

    from_gameweek: int | None = None
    to_gameweek: int | None = None
    weeks: int = 0
    clubs: list[ClubFixtureRun] = []
    # Names that matched no club at all, echoed back rather than silently
    # dropped -- a missing club in the output otherwise reads as a club with
    # no fixtures, which is a blank, which is a decision.
    unknown_clubs: list[str] = []
    note: str | None = None


class SquadViolation(BaseModel):
    """Issue 22: one broken squad rule.

    A slug plus prose, never a bare boolean: "Illegal: Salah is a MID,
    Watkins is a FWD" is actionable, `false` is not, and a model handed
    `false` will guess at the reason."""

    rule: str
    message: str


class SquadMoveVerdict(BaseModel):
    """Issue 22: whether a proposed transfer can be made at all.

    `legal` false means FPL will not accept the move -- it is not a judgement
    about whether the move is wise. That distinction is load-bearing: ticket
    11's `PUSHBACK:` says "legal, but I think you are wrong" and invites an
    argument; a violation here ends one.
    """

    legal: bool
    player_out: str | None = None
    player_in: str | None = None
    violations: list[SquadViolation] = []
    # The selling price of the player out plus the bank. None when no
    # finances have been ingested -- unknown, never assumed.
    budget: float | None = None
    price_in: float | None = None
    shortfall: float | None = None
    # False when FPL's team value shows at least one squad player sells for
    # less than their listed price. The budget above is then an upper bound,
    # so a move inside the gap is reported as unverified rather than refused.
    affordability_exact: bool = False
    # Issue 22: derived from ingested history, never asked of the user.
    free_transfers: int | None = None
    is_hit: bool = False
    hit_cost: int = 0
    summary: str
    note: str | None = None


class PlayerRisk(BaseModel):
    player_id: int
    player_name: str
    status: str | None = None
    news: str | None = None
    # Issue 07: the club's next ingested fixture, null if none is scheduled
    # (a blank gameweek) or the club has no ingested fixtures at all.
    next_opponent: str | None = None
    next_fixture_difficulty: int | None = None
    injury_risk: bool
    fixture_risk: bool


class SquadRiskSummary(BaseModel):
    players: list[PlayerRisk]


class RecommendationPick(BaseModel):
    player_id: int
    player_name: str


class Alternative(BaseModel):
    player_id: int
    player_name: str
    reason: str


def deep_link(url: str, start_seconds: int | None) -> str:
    """A source url plus the second the claim was made, when we know it.

    The separator is chosen rather than assumed: a stored YouTube url is
    `watch?v=<id>` and already carries a query string, while an X post url is
    a plain path. A start of 0 is treated as no timestamp -- linking to the
    very start of a video is what the bare url already does.
    """
    if not start_seconds or start_seconds < 0:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}t={start_seconds}s"


class Citation(BaseModel):
    """Issue 17: where one evidence line came from.

    Every field is copied out of a tool result, never taken from what the
    model wrote -- the same discipline ADR-0004 already applies to
    `submit_recommendation`'s player-id enum. A model-authored URL is a
    hallucinated URL, and a citation the user cannot check is worse than no
    citation at all.
    """

    creator_name: str
    trust_tier: str
    # Already a deep link when start_seconds is known. Built once, here,
    # rather than in each consumer, so the dashboard and any later export
    # can never disagree about where a claim points.
    url: str
    # Seconds into a video where the claim was made. None for an X post, and
    # for a video whose Stance was extracted without a [MM:SS] marker.
    start_seconds: int | None = None
    # ISO date string. None where we genuinely do not know: ticket 16 only
    # records published_at for YouTube, so X-sourced citations carry no date
    # rather than a fetched_at standing in for one.
    published_at: str | None = None

    @classmethod
    def from_stance(cls, view) -> "Citation | None":
        """None when the stance has no source url to point at -- a Stance
        view built by hand (a test, a fixture) rather than read from
        creator_content. An uncitable stance yields no citation instead of a
        citation to nowhere."""
        source_url = getattr(view, "source_url", None)
        if not source_url:
            return None
        return cls(
            creator_name=view.creator_name,
            trust_tier=view.trust_tier,
            url=deep_link(source_url, view.start_seconds),
            start_seconds=view.start_seconds,
            published_at=view.published_at,
        )


class Evidence(BaseModel):
    """Issue 17: one evidence line, and optionally who said it.

    `text` is the model's own prose and is always present. `citation` is
    absent for a purely statistical line (xG, a price move, a fixture
    difficulty) -- that line is not weaker for it, and inventing a source to
    fill the field would make it so.
    """

    text: str
    citation: Citation | None = None


# What a Recommendation actually decides. Needed because `pick` alone cannot
# express "do nothing": before this existed every answer had to name a player,
# so a correct "roll your transfer this week" still arrived carrying a player
# id, and the dashboard rendered that player as though it were the answer.
DecisionKind = Literal["captain", "transfer", "roll", "chip", "other"]


class Recommendation(BaseModel):
    decision: DecisionKind
    # None exactly when the decision names no player -- a "roll" (bank the
    # free transfer) is the common case. For "transfer" this is the player
    # coming IN. Consumers must not render a pick that is None as a pick.
    pick: RecommendationPick | None = None
    confidence: float
    evidence: list[Evidence]
    risks: list[str]
    # None when there is no genuine second option. Never synthesise one to
    # fill the field.
    alternative: Alternative | None = None


class Creator(BaseModel):
    id: int
    name: str
    # Issue 16: None for a creator followed only on YouTube -- platform
    # identities live in creator_sources now.
    x_handle: str | None = None
    trust_tier: str


class CreatorStanceView(BaseModel):
    creator_name: str
    trust_tier: str
    stance: str
    confidence: float
    reason: str
    # Issue 17: the citation half. citation_id is creator_stances.id -- the
    # handle submit_recommendation accepts, so an evidence line's attribution
    # is looked up from this row rather than written by the model. All four
    # are optional because a view built by hand in a test has no DB row
    # behind it; a view from get_creator_opinions always fills them.
    citation_id: int | None = None
    source_url: str | None = None
    start_seconds: int | None = None
    published_at: str | None = None
    # Issue 18: the recency half. `recency` is the phrase the model actually
    # reads ("9 days ago, for GW1 -- already played, this predates the GW2
    # deadline"); the raw numbers sit alongside it for anything that wants to
    # compute rather than read. Optional for the same reason as the citation
    # fields: a view built by hand in a test has no DB row behind it.
    gameweek: int | None = None
    age_days: float | None = None
    recency: str | None = None
    is_current_gameweek: bool = False


class CreatorOpinions(BaseModel):
    player_name: str
    # Issue 18: ordered by trust tier scaled by recency decay (see
    # stance_ranking), so the freshest high-trust opinion leads. Was issue
    # 06's trust-tier-only sort, which was a no-op while every creator was
    # 'high' and left a pre-season take level with a deadline-eve one.
    stances: list[CreatorStanceView]
    # Issue 18: a bounded list must say what it is a slice of, or a truncated
    # view gets mistaken for the whole picture.
    total_matched: int = 0
    returned: int = 0
    # Stances that matched the player but were left out as too old to bear on
    # the gameweek being planned. Counted, never deleted -- ticket 19's
    # week-over-week consensus and ADR-0012's outcome history both read them.
    older_not_shown: int = 0
    planning_gameweek: int | None = None
    # Issue 18: "no trusted creator has mentioned him since GW1" is a real,
    # useful answer. Set when the player has stances on record but none recent
    # enough to return, so an empty list is never ambiguous between "nobody
    # covers him" and "nobody has covered him lately".
    note: str | None = None


class Instinct(BaseModel):
    text: str
    logged_at: str


class Instincts(BaseModel):
    # Issue 13: always scoped to the current gameweek only -- never a
    # cross-gameweek history.
    notes: list[Instinct]


class StanceCount(BaseModel):
    """Issue 19: one stance kind within one gameweek. Both numbers are kept
    because they answer different questions: `creators` is how many people
    hold the view, `stances` is how loudly it was said. Six posts from one
    account is `creators=1, stances=6`, and collapsing that to a single
    number in either direction would mislead."""

    stance: str
    creators: int
    stances: int


class ConsensusWindow(BaseModel):
    """Issue 19: one gameweek's slice of the discourse about one player.

    The positive/negative/neutral counts take each creator's *most recent*
    stance in the window as their position, so somebody who moved from hold to
    buy mid-week counts once, as a buy. `by_stance` keeps the unreduced view
    alongside it."""

    gameweek: int | None = None
    creators_covering: int = 0
    creators_positive: int = 0
    creators_negative: int = 0
    creators_neutral: int = 0
    by_stance: list[StanceCount] = []
    creator_names: list[str] = []
    stances: int = 0


class ConsensusChange(BaseModel):
    """Current minus previous, per bucket. Positive means more creators."""

    covering: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0


class CreatorConsensus(BaseModel):
    """Issue 19: how many trusted creators are on a player, and how that
    moved. See consensus.py for why the unit is creators rather than Stances
    and why the derivative matters more than the level.

    Carries no citation_id anywhere, deliberately: a count is an aggregate
    like a season xG total, and an evidence line resting on it is correctly
    uncited (ADR-0014). get_creator_opinions remains the citable path."""

    player_name: str
    roster_size: int
    planning_gameweek: int | None = None
    current: ConsensusWindow
    previous: ConsensusWindow
    change: ConsensusChange
    movement: str
    # The sentence written for the reasoning layer to read and quote, in the
    # same spirit as CreatorStanceView.recency.
    summary: str
    # Issue 13/19: the user's own live Instincts that name this player, and
    # what to do about them. Empty and None when they have logged nothing
    # about him this gameweek.
    instincts: list[Instinct] = []
    instinct_note: str | None = None
    # How each Stance was placed in a gameweek: "deadlines" (the gameweeks
    # table), "first_kickoff" (a proxy, when no ingestion has populated it
    # yet), or "stored_tag" (last resort, and known unreliable -- see
    # consensus.py). Reported rather than assumed, because the counts mean
    # something weaker under the fallbacks.
    gameweek_source: str = "deadlines"
    # Stances about this player that could not be placed in any gameweek at
    # all -- no publish date. Counted so a thin window is never mistaken for
    # a quiet week.
    undated_stances: int = 0
    note: str | None = None


class SemanticCreatorMatch(BaseModel):
    # Issue 09: a pgvector nearest-neighbour hit, resolved back to its
    # Creator Stance's structured fields (ADR-0005) -- not just raw text.
    creator_name: str
    trust_tier: str
    stance: str
    confidence: float
    reason: str
    matched_text: str
    # Issue 17: same citation half as CreatorStanceView. start_seconds
    # prefers the Stance's own timestamp and falls back to the matched
    # chunk's -- the chunk is what similarity actually found, so it is the
    # honest second choice when the Stance itself was extracted untimed.
    citation_id: int | None = None
    source_url: str | None = None
    start_seconds: int | None = None
    published_at: str | None = None
    # Issue 18: same recency half as CreatorStanceView.
    gameweek: int | None = None
    age_days: float | None = None
    recency: str | None = None
    is_current_gameweek: bool = False


class SemanticCreatorOpinions(BaseModel):
    query: str
    # Issue 09/18: candidates are still gathered by embedding similarity --
    # relevance is what makes a semantic hit worth returning at all -- but
    # the final ordering multiplies that similarity by the same recency/trust
    # weight get_creator_opinions uses. Ranking a loose query on recency
    # alone would surface today's most irrelevant post.
    matches: list[SemanticCreatorMatch]
    total_matched: int = 0
    returned: int = 0
    planning_gameweek: int | None = None


class PricePrediction(BaseModel):
    player_id: int
    player_name: str
    direction: str  # "rise" | "fall"
    ownership: float | None = None
    net_transfers_event: int
    evidence: str


class PriceChangePredictions(BaseModel):
    # Issue 08: each list sorted most-likely-first.
    rising: list[PricePrediction]
    falling: list[PricePrediction]


# Issue 23: what a checklist item is allowed to say about itself.
#
# Three states rather than a boolean, because "unknown" is the honest answer
# far more often than the dashboard would like -- before any ingestion, and
# for anything resting on data FPL does not publish. Collapsing unknown into
# either ok or attention makes the screen lie in one direction or the other.
#
# `attention` is deliberately not named `warning` or `error`. The ticket asks
# each item to state the fact and leave the call to the user, and
# preferences.md is emphatic that the final decision is theirs -- so this
# means "there is something here worth your eyes", never "you must fix this".
ChecklistState = Literal["ok", "attention", "unknown"]


class ChecklistItem(BaseModel):
    """Issue 23: one checkable fact about the squad as it stands.

    `detail` is written as a statement, never an instruction: "Two players in
    your XI are flagged: X (injured), Y (doubtful)" rather than "Bench X and
    Y". The app's job on this screen is to make the situation visible, not to
    make the decision -- the AI suggestion card alongside it is where advice
    belongs.
    """

    key: str
    title: str
    state: ChecklistState
    detail: str
    # The players the item is about, so the UI can highlight them without
    # re-parsing `detail`. Empty when the item is about the squad as a whole
    # (the bank) or when nothing triggered it.
    players: list[str] = []


class DeadlineCountdown(BaseModel):
    """Issue 23: how long until the user can no longer act.

    `gameweek` is the gameweek of the next UNPASSED deadline, which is not
    always the planning gameweek -- mid-gameweek, on a Saturday afternoon,
    the planning gameweek is one whose deadline went on Friday. Both are
    reported so the screen can never imply the user can still change a locked
    team.
    """

    gameweek: int | None = None
    planning_gameweek: int | None = None
    # ISO 8601. None when no calendar is loaded, or the season's last
    # deadline has passed.
    deadline: str | None = None
    seconds_remaining: int | None = None
    # Where the deadline came from: "deadlines" (the gameweeks table),
    # "first_kickoff" (a proxy that runs ~90 minutes LATE, so the countdown
    # is generous), or "none". Reported because a proxy countdown that
    # overstates the time left is the one failure mode that costs points.
    source: str = "none"
    note: str | None = None


class DeadlineChecklist(BaseModel):
    """Issue 23: the deadline screen's whole payload.

    Reads only -- no Claude call and no live FPL API call, so opening the
    dashboard is never a billed event (ADR-0002 keeps the AI suggestion
    on-demand and cached; this sits alongside it).
    """

    # The gameweek every item below is about: the one whose deadline has not
    # passed, falling back to the planning gameweek when no calendar exists.
    target_gameweek: int | None = None
    # The gameweek the XI/captain shown were actually read from. Trails
    # target_gameweek in the pre-deadline window, because FPL only publishes
    # picks once a deadline has locked.
    squad_gameweek: int | None = None
    countdown: DeadlineCountdown
    items: list[ChecklistItem] = []
    # Scoped to the user's own players, not the global rising/falling lists:
    # on this screen the question is what is happening to the squad they
    # have, not what is happening to the market.
    price_risk: PriceChangePredictions
    attention_count: int = 0
    unknown_count: int = 0


class TransferOption(BaseModel):
    player: Player
    # Issue 11: reuses get_creator_opinions's exact-name lookup, not a new
    # query -- same trust-tier-sorted shape.
    creator_stances: list[CreatorStanceView]
    next_opponent: str | None = None
    next_fixture_difficulty: int | None = None
    injury_risk: bool


class TransferComparison(BaseModel):
    player_out: TransferOption
    player_in: TransferOption
    is_hit: bool
    hit_cost: int  # 4 if is_hit else 0 -- FPL's fixed extra-transfer cost
    # Issue 11: a *directional* signal (xG + xA + form, units that don't
    # actually compose into points), not a points projection -- deliberately
    # not named expected_gain so nothing downstream reads it as one.
    gain_signal: float
    net_gain_signal: float  # gain_signal - hit_cost
    # False when either player is missing xg/xa/form -- gain_signal silently
    # reads as 0.0 in that case, which must not be read as "no gain",
    # especially pre-season when this is the common case.
    data_complete: bool
    # Issue 22: the legality check this comparison passed. Present so the
    # affordability and free-transfer figures travel with the comparison
    # rather than needing a second tool call -- an ILLEGAL move never reaches
    # this type at all, it is returned as a bare SquadMoveVerdict instead.
    verdict: SquadMoveVerdict | None = None


class PlanStep(BaseModel):
    # Issue 12: an absolute FPL gameweek number, anchored to
    # Team.current_gameweek by whatever built the plan -- not a relative
    # "week 1 of the plan" offset.
    gameweek: int
    decision: DecisionKind
    # Same "None means this step names no player" contract as
    # Recommendation.pick -- a plan whose week 2 is "roll" has no pick. For a
    # 'transfer' step this is the player coming IN.
    pick: RecommendationPick | None = None
    # Issue 25: the player being SOLD. Absent from ticket 12's design, and
    # that absence is what made a plan uncheckable: without knowing who
    # leaves, the budget after step 2 is unknowable, so step 3's affordability
    # cannot be tested at all. Optional and defaulted so every plan stored or
    # cached before this field existed still loads.
    player_out: RecommendationPick | None = None
    evidence: list[Evidence]
    risks: list[str]


class TransferPlan(BaseModel):
    # Issue 12: a longer Horizon than a single-gameweek Recommendation --
    # returned as a distinct type rather than a field on Recommendation, so
    # a single-gameweek question's response shape is untouched. horizon is
    # a free-text label the Reasoning implementation chose (e.g.
    # "multi-week", "season-long"), descriptive only -- never persisted
    # (CONTEXT.md's Horizon is resolved live from the question each time).
    horizon: str
    confidence: float
    steps: list[PlanStep]


# Issue 25: a Roadmap is a TransferPlan the user is FOLLOWING.
#
# The distinction is the ticket's whole premise. A TransferPlan is one answer
# to one question and is complete the moment it renders. A Roadmap persists,
# is versioned, and can therefore be checked next week against a world that
# has moved -- which is what makes "did my thinking change, or did the facts?"
# an answerable question rather than a feeling.
RoadmapStatus = Literal["live", "completed", "superseded"]


class StepViolation(BaseModel):
    """Issue 25: one broken rule, anchored to the step that broke it.

    Carries both `gameweek` and `step_index` because a plan may hold several
    steps in the same gameweek (a free transfer plus a hit), so the gameweek
    alone does not identify a step. `step_index` indexes the plan's steps AS
    SUBMITTED, not as sorted -- the model has to be able to find the step it
    wrote."""

    step_index: int
    gameweek: int
    rule: str
    message: str


class StepCost(BaseModel):
    """Issue 25: what one step does to the running state.

    Reported per step rather than only in aggregate because "you are 0.3m
    short in GW7" is actionable and "this plan does not work" is not. `bank_after`
    is the figure the NEXT step is checked against, so a plan that drifts
    negative shows exactly where.
    """

    step_index: int
    gameweek: int
    decision: str
    player_in: str | None = None
    player_out: str | None = None
    # Free transfers unspent at the START of this step's gameweek, after
    # accrual for the weeks since the last step.
    free_transfers_before: int | None = None
    is_hit: bool = False
    hit_cost: int = 0
    bank_before: float | None = None
    bank_after: float | None = None


class PlanValidation(BaseModel):
    """Issue 25: the deterministic verdict on a whole multi-week plan.

    `legal` false means FPL would not accept this sequence -- not that it is
    a bad plan. Same distinction `SquadMoveVerdict` draws for a single move
    (ticket 22): a violation ends an argument, pushback invites one.
    """

    legal: bool
    violations: list[StepViolation] = []
    steps: list[StepCost] = []
    # Points spent on hits across the whole plan. Stated once, because a plan
    # whose steps are individually reasonable can still cost 12 points that
    # nobody added up.
    total_hit_cost: int = 0
    transfers_planned: int = 0
    # False when FPL's team value shows a squad player sells for less than
    # their listed price. Every budget figure above is then an upper bound.
    affordability_exact: bool = False
    summary: str = ""
    note: str | None = None


class FixtureChange(BaseModel):
    """Issue 25: a fixture that moved under a stored plan.

    `fixtures` is wholesale replaced on every run_ingestion, so a roadmap
    stored three weeks ago can be pointing at a gameweek that has since
    become a blank -- silently, with no record that anything happened. The
    snapshot taken at store time is what turns "this is a blank" into "this
    BECAME a blank", which is the fact the user needs.
    """

    club: str
    gameweek: int
    was: int
    now: int
    kind: str  # "became_blank" | "became_double" | "fixture_added" | "fixture_removed"
    message: str


class RoadmapChange(BaseModel):
    """One reason a stored step no longer holds."""

    step_index: int
    gameweek: int
    kind: str  # "elapsed" | "injury" | "fixture" | "illegal" | "price"
    message: str
    player_name: str | None = None


class Roadmap(BaseModel):
    id: int
    team_id: int
    version: int
    status: RoadmapStatus
    plan: "TransferPlan"
    from_gameweek: int | None = None
    prompt: str | None = None
    created_at: str
    superseded_at: str | None = None
    supersede_reason: str | None = None


class RoadmapCheckIn(BaseModel):
    """Issue 25: what changed since the roadmap was made.

    Deliberately reports and stops, like ticket 23's checklist: it names the
    steps that no longer hold and why, and does not write a replacement. A
    revision is a reasoning call the user chooses to make, not something the
    app does to their plan behind their back.
    """

    roadmap: Roadmap | None = None
    planning_gameweek: int | None = None
    # Steps whose gameweek has already gone. Not a problem -- just no longer
    # actionable, and separated so a plan that has simply progressed does not
    # read as a plan that has broken.
    elapsed_steps: list[int] = []
    remaining_steps: list[int] = []
    changes: list[RoadmapChange] = []
    fixture_changes: list[FixtureChange] = []
    # The plan's remaining steps re-checked against today's squad, prices and
    # fixtures. None when there are no remaining steps to check.
    revalidation: PlanValidation | None = None
    still_valid: bool = True
    summary: str = ""
    note: str | None = None


# ---------------------------------------------------------------------------
# Issue 27: a reasoning exchange that can be argued with.


class ConversationReply(BaseModel):
    """Issue 27: the model speaking rather than finalising.

    Until now `ClaudeReasoning` had exactly two exits -- `submit_recommendation`
    and `submit_plan` -- and `tool_choice: {"type": "any"}` forces a tool call
    every turn. So the model could not ask "which of those two did you mean?"
    or engage with an objection; it could only answer. That is the specific
    feeling of being talked at rather than advised, and `preferences.md` says
    the final call must be justifiable as the user's own reasoning -- which is
    impossible for a decision they were unable to question.

    This is a third terminal, and it carries no pick by design. ADR-0004 is
    untouched: a decision still only ever comes from a submit tool whose
    player ids are an enum of players actually looked up. Prose is prose.
    """

    text: str


class PlanRevision(BaseModel):
    """Issue 27: a change to ONE step of the Roadmap the user is following.

    A fourth terminal, and the narrowest. `submit_plan` answers "what should I
    do over the next five weeks"; this answers "actually, why am I rolling in
    GW5?" -- where rebuilding the whole plan would be the wrong response
    twice over. It discards the reasoning behind the four steps the user did
    not question, and it silently changes steps they were happy with.

    Carries the gameweek rather than an index, because a step's identity to
    the user is the week it happens in, and because an index into a list the
    model was shown is exactly the kind of positional reference that goes
    wrong when the list is re-sorted. `merge_step` refuses a gameweek that
    names no step or more than one.

    ADR-0004 is untouched: `step` is built from the same recorded-id enum as
    a `submit_plan` step, so a revision can no more invent a player than a
    plan can. And the merged plan is put through the same `plan_problems`
    walk before it is stored -- a legal-looking step can spend money a later
    step needed, which is exactly what the cumulative check is for.
    """

    gameweek: int
    step: "PlanStep"
    # What changed and why, for the user. The revised plan renders as a plan;
    # this is the sentence explaining why it is different from the one they
    # were looking at a moment ago.
    note: str


class ConversationTurn(BaseModel):
    """One entry in the transcript.

    A turn holds whichever of the three shapes the exchange produced, rather
    than flattening them to text: the dashboard already knows how to render a
    Recommendation and a TransferPlan properly (evidence lines, citations,
    confidence), and downgrading them to prose inside a chat log would throw
    away the cited-evidence work of ticket 17.
    """

    role: Literal["user", "assistant"]
    # The user's own words, or the model's reply when it chose to talk rather
    # than finalise. None on an assistant turn that carried a decision.
    text: str | None = None
    recommendation: Recommendation | None = None
    plan: TransferPlan | None = None
    created_at: str


class Conversation(BaseModel):
    """Issue 27: one live exchange per team.

    Gameweek-scoped, and discarded when it rolls over -- the same rule as an
    Instinct (ticket 13). An argument about a deadline that has passed is not
    context, it is noise, and carrying it forward would quietly re-anchor next
    week's reasoning on last week's case.
    """

    team_id: int
    gameweek: int | None = None
    turns: list[ConversationTurn] = []


# ---------------------------------------------------------------------------
# Issue 28: what the discourse is actually on.


class AttentionRow(BaseModel):
    """Issue 28: one player's place in this week's conversation.

    Reuses ticket 19's `ConsensusWindow` shape rather than a flatter one, so
    the board and the per-player panel cannot drift into disagreeing about
    what "covered" means.
    """

    player_name: str
    current: ConsensusWindow
    previous: ConsensusWindow
    change: ConsensusChange
    movement: str
    # Coverage inside the last 48 hours, separated from coverage carried over
    # from earlier in the gameweek. A theme running all week is background; one
    # that appeared on Thursday is why the user opened the page.
    creators_last_48h: int = 0
    creator_names_last_48h: list[str] = []
    summary: str


class AttentionBoard(BaseModel):
    """Issue 28: the players trusted creators are talking about, ranked by how
    much that MOVED rather than by how loud it is.

    Deliberately not a shortlist to buy from. Ticket 19 scoped consensus to
    the user's own instinct-players precisely so a bandwagon meter could not
    become a way to go shopping for bandwagons; this inverts that scoping, so
    it carries the framing itself -- `preferences.md` treats heavy coverage as
    a risk, not an endorsement.
    """

    planning_gameweek: int | None = None
    rows: list[AttentionRow] = []
    # A bounded list must say what it is a slice of, or a truncated view gets
    # mistaken for the whole picture (same rule as CreatorOpinions).
    total_players: int = 0
    returned: int = 0
    roster_size: int = 0
    # "deadlines" | "first_kickoff" | "stored_tag" -- the counts mean something
    # weaker under the fallbacks, so the board says which it used.
    gameweek_source: str = "deadlines"
    note: str | None = None


# ---------------------------------------------------------------------------
# Issue 29: what creators are collectively talking about.


class Theme(BaseModel):
    """Issue 29: one cluster of related creator content.

    Two strings, and the distinction is the point. `title` NAMES the theme;
    `label` QUOTES it.

    `label` is a REAL CHUNK of creator text -- never generated prose. ADR-0014
    exists because a model-authored citation is a hallucinated citation, and a
    model-authored theme label is the same hallucination surface: plausible,
    unverifiable, and wrong in a way that looks authoritative. A quotation has
    a source and a timestamp, so it is checkable.

    `title` was added 2026-09-04, when the panel was first opened in a
    browser and turned out to be headed by things like "Whereas with now,
    right, you have this high floor created for you by the variables discussed
    beforehand." A quotation is evidence, and evidence does not make a
    heading.

    It is nonetheless not a violation of the rule above, because it is
    ASSEMBLED rather than written: every element is either a term from a
    closed FPL vocabulary matched literally in the creators' own words, or a
    player name resolved through `creator_stances`. No model is involved, and
    when it recognises nothing it reports the count rather than inventing a
    subject.
    """

    title: str = ""
    label: str
    # Both numbers, never one. `creators` is how many people hold the view;
    # `mentions` is how loudly it was said. Six posts from one account is
    # creators=1, mentions=6, and collapsing that either way misleads.
    creators: int
    creator_names: list[str] = []
    mentions: int
    # Resolved through creator_content -> creator_stances, not by matching text
    # against player names.
    players: list[str] = []
    # The medoid's own source. None when the content row has no url to point
    # at, same rule as Citation.from_stance.
    citation: Citation | None = None
    # First appeared inside the recency window rather than carried over from
    # earlier. The derivative again: a theme running three weeks is
    # background, one that appeared on Thursday is news.
    is_new: bool = False
    first_seen: str | None = None


class ThemeBoard(BaseModel):
    planning_gameweek: int | None = None
    themes: list[Theme] = []
    # What the clustering actually looked at, so a thin result is never
    # mistaken for a quiet week.
    chunks_considered: int = 0
    # The cosine distance below which two chunks are treated as related.
    # Reported because it is the one tuning knob that changes what a theme is.
    threshold: float = 0.0
    note: str | None = None


# ---------------------------------------------------------------------------
# The refresh surface.
#
# ADR-0002 keeps ingestion on demand, and there is nothing wrong with a CLI --
# but a thing done every Friday should not require a terminal, and until now
# the app could not tell the user how old what they were looking at was. These
# models are the two halves of fixing that: what a run IS, and how stale the
# data is when no run explains it.

IngestionKind = Literal["fpl", "creators"]

IngestionRunStatus = Literal["running", "succeeded", "failed", "abandoned"]


class IngestionRun(BaseModel):
    """One attempt at refreshing one source.

    `summary` is whichever pipeline summary the run produced, as a plain dict
    -- the two pipelines count entirely different things and each grows a
    field per ticket, so typing it here would mean editing this file every
    time a counter is added, to describe something that is only ever
    rendered.

    `error` carries the exception text of a failed run. A refresh that failed
    silently is worse than no refresh at all: the freshness stamp would go
    green while the data underneath it did not move.
    """

    id: int
    kind: IngestionKind
    status: IngestionRunStatus
    trigger: Literal["api", "cli"] = "api"
    started_at: str
    finished_at: str | None = None
    # Wall-clock seconds. Present on a finished run only, and worth surfacing
    # because it is the honest answer to "how long will this take" the next
    # time -- the creator run's duration swings by an order of magnitude with
    # how many videos have transcripts.
    duration_seconds: float | None = None
    summary: dict | None = None
    error: str | None = None


class DataFreshness(BaseModel):
    """How old one source's data is, and how confidently we know that.

    `source` is the load-bearing field, and it exists for the same reason
    `DeadlineCountdown.source` does. Three answers, which must not be
    collapsed:

    - `run` -- a recorded successful run. `last_success` is exact.
    - `proxy` -- no run is recorded, but the data is plainly there, so its
      own timestamps are used instead (`gameweek_state.updated_at` for FPL
      data, the newest `creator_content.fetched_at` for creators). Every run
      predating the ledger lands here, and the figure is a lower bound: it
      says when data last CHANGED, which a run that fetched nothing new does
      not do.
    - `none` -- nothing has ever been ingested. Not the same as "old", and a
      screen that renders it as "0 days ago" would be inventing a refresh
      that never happened.
    """

    kind: IngestionKind
    last_success: str | None = None
    source: Literal["run", "proxy", "none"] = "none"
    age_seconds: float | None = None
    # True once the data is old enough that the panels resting on it should
    # say so. Computed server-side so the threshold is defined once, next to
    # the reason for it, rather than in each component that renders a stamp.
    is_stale: bool = False
    # Plain-language version of all of the above, because the combination of
    # source and age is the part a stamp cannot show.
    note: str | None = None


class IngestionStatus(BaseModel):
    """Everything the refresh control needs, in one read.

    Deliberately one endpoint covering both kinds rather than one per kind:
    the control renders them side by side, and two requests could disagree
    about whether a run is in flight.
    """

    runs: list[IngestionRun] = []
    freshness: list[DataFreshness] = []
    # Whether a run is in flight right now, so the UI can poll rather than
    # having to infer it from statuses.
    running: list[IngestionKind] = []
