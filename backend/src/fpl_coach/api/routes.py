import logging
import os
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from psycopg import Connection
from pydantic import BaseModel

from fpl_coach.attention import build_attention_board
from fpl_coach.checklist import build_checklist
from fpl_coach.conversation import (
    assistant_turn,
    clear_conversation,
    load_conversation,
    save_conversation,
    user_turn,
)
from fpl_coach.db import get_connection
from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.jobs import execute_run
from fpl_coach.ingestion.runs import (
    KINDS,
    RunAlreadyRunning,
    ingestion_status,
    start_run,
)
from fpl_coach.ingestion.voyage_client import VoyageClient
from fpl_coach.gameweek import planning_gameweek
from fpl_coach.instincts import instinct_players, is_instinct, log_instinct
from fpl_coach.models import (
    AttentionBoard,
    Conversation,
    ConversationReply,
    CreatorConsensus,
    DeadlineChecklist,
    IngestionRun,
    IngestionStatus,
    Instincts,
    PlanRevision,
    Recommendation,
    Roadmap,
    RoadmapCheckIn,
    Team,
    ThemeBoard,
    TransferPlan,
)
from fpl_coach.outcomes import log_recommendation
from fpl_coach.plan_rules import StepNotRevisable, merge_step
from fpl_coach.tools.get_roadmap import make_get_roadmap_tool
from fpl_coach.roadmap import (
    check_in,
    get_live_roadmap,
    get_roadmap_versions,
    plan_problems,
    save_roadmap,
)
from fpl_coach.preferences import (
    append_preference,
    build_system_prompt,
    extract_remember_instruction,
    load_preferences,
)
from fpl_coach.reasoning import ClaudeReasoning, FakeReasoning, Reasoning
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.suggestions import get_cached_suggestion, save_suggestion
from fpl_coach.themes import build_themes
from fpl_coach.tools import (
    make_compare_players_tool,
    make_get_chip_status_tool,
    make_get_creator_consensus_tool,
    make_get_creator_opinions_tool,
    make_get_fixture_run_tool,
    make_get_injury_news_tool,
    make_get_instincts_tool,
    make_get_my_team_tool,
    make_get_player_tool,
    make_predict_price_changes_tool,
    make_recommend_transfers_tool,
    make_search_creator_opinions_tool,
    make_validate_squad_move_tool,
)
from fpl_coach.tools.get_creator_consensus import get_creator_consensus
from fpl_coach.tools.get_instincts import get_instincts
from fpl_coach.tools.get_my_team import get_my_team
from fpl_coach.tools.validate_squad_move import validate_incoming

router = APIRouter()

_log = logging.getLogger(__name__)

MY_TEAM_ID = 1  # v1 MVP scope (initial-plan.md): single user, one team.

# Issue 15: the Team dashboard's unprompted suggestion -- deliberately framed
# as a single-gameweek ask (not "plan my season") so Claude's own
# submit_recommendation-vs-submit_plan judgment (claude.py) is nudged toward
# a Recommendation, matching what the dashboard is meant to show. Nothing
# forces this -- a TransferPlan is still a valid response and the route
# handles either, same as /recommendations.
_SUGGESTION_PROMPT = (
    "Based on everything currently known about my squad, fixtures, injuries, and trusted "
    "creator opinions, is there anything I should know or consider for this gameweek? Give "
    "me your single most useful captain, transfer, or hold recommendation right now, without "
    "me having to ask."
)


class AskRequest(BaseModel):
    question: str


class RememberAck(BaseModel):
    remembered: str


def get_reasoning() -> Reasoning:
    """Overridden in tests via app.dependency_overrides (Seam-1: FakeReasoning)."""
    if os.environ.get("REASONING_IMPL", "claude") == "fake":
        return FakeReasoning()
    return ClaudeReasoning()


def _build_tools(conn: Connection) -> list[ToolSpec]:
    return [
        make_get_my_team_tool(conn, MY_TEAM_ID),
        make_get_player_tool(conn, FPLClient()),
        make_compare_players_tool(conn),
        make_get_injury_news_tool(conn, MY_TEAM_ID),
        make_get_fixture_run_tool(conn, MY_TEAM_ID),
        make_get_creator_opinions_tool(conn, MY_TEAM_ID),
        make_get_creator_consensus_tool(conn, MY_TEAM_ID),
        make_get_chip_status_tool(conn, FPLClient(), MY_TEAM_ID),
        make_predict_price_changes_tool(conn),
        make_get_instincts_tool(conn, MY_TEAM_ID),
        make_get_roadmap_tool(conn, MY_TEAM_ID),
        make_search_creator_opinions_tool(conn, VoyageClient(), MY_TEAM_ID),
        make_recommend_transfers_tool(conn, MY_TEAM_ID),
        make_validate_squad_move_tool(conn, MY_TEAM_ID),
    ]


def _make_validator(conn: Connection):
    """Issue 22: the legality gate on the finalisation path.

    `submit_recommendation` names only the player coming IN, so this cannot
    be a full validation -- it is a POSSIBILITY check (validate_incoming):
    already owned, no squad player of that position to sell, no club slot any
    legal sale could free, and unaffordable even after selling the most
    expensive player of that position. Failing any of those means no legal
    version of the move exists whoever the user had in mind, which is exactly
    the class of answer that must never reach them.

    A specific bad pairing can still get through, and that is the right
    trade: the model cannot tell the validator which pairing it meant, and a
    false refusal would teach it to route around the check. `recommend_transfers`
    is where a named pairing is fully validated.

    Issue 25 removes that limitation for a PLAN. `submit_plan` names both
    sides of every transfer, so a plan is checked properly -- walked in
    gameweek order against a running squad, bank and free-transfer count. A
    plan's step 3 was never less wrong for being three weeks away; now it is
    no longer unchecked either.
    """

    def validate(result: Recommendation | TransferPlan | PlanRevision) -> list[str]:
        # Issue 27: a revision is checked as the plan it would produce, not
        # as one step. Merged against the LIVE roadmap here rather than in
        # the route so a refusal still reaches the model through ticket 22's
        # is_error channel and the must_submit latch -- validating after the
        # loop had exited would mean the user seeing the failure instead of
        # the model getting a turn to fix it.
        if isinstance(result, PlanRevision):
            live = get_live_roadmap(conn, MY_TEAM_ID)
            if live is None:
                return [
                    "There is no stored roadmap to revise. Use submit_plan to "
                    "create one."
                ]
            try:
                merged = merge_step(live.plan, result.gameweek, result.step)
            except StepNotRevisable as exc:
                return [str(exc)]
            return plan_problems(conn, merged, MY_TEAM_ID)

        # Issue 25: a plan gets the full cumulative walk instead of a
        # per-step possibility check. It names both sides of every transfer,
        # so the budget, the free transfers and the squad shape can all be
        # carried forward -- which is the whole thing ticket 12 could not do
        # and the reason a plan's step 3 was never checked against its own
        # steps 1 and 2.
        if isinstance(result, TransferPlan):
            return plan_problems(conn, result, MY_TEAM_ID)

        if result.decision != "transfer" or result.pick is None:
            return []
        return validate_incoming(conn, result.pick.player_name, MY_TEAM_ID)

    return validate


def _log_result(conn: Connection, result: Recommendation | TransferPlan) -> None:
    if isinstance(result, TransferPlan):
        # Issue 12: only the nearest step is an actual commitment right
        # now -- record_pending_outcomes (ADR-0012) only ever resolves
        # the immediately-prior gameweek's rows, so logging every
        # future-dated step here would leave the rest as permanently
        # unresolved speculation, not real history. `min` rather than
        # `steps[0]`: nothing in the submit_plan schema or its
        # description requires Claude to emit steps in gameweek order.
        nearest = min(result.steps, key=lambda step: step.gameweek)
        if nearest.pick is not None:
            log_recommendation(
                conn,
                MY_TEAM_ID,
                nearest.pick.player_id,
                nearest.pick.player_name,
                gameweek=nearest.gameweek,
            )
    # A pick-less decision (a "roll") names no player, and
    # recommendation_log exists to match a named player against what that
    # player went on to do -- so there is nothing to log and nothing for
    # record_pending_outcomes to later resolve.
    elif result.pick is not None:
        log_recommendation(conn, MY_TEAM_ID, result.pick.player_id, result.pick.player_name)


@router.post(
    "/recommendations",
    response_model=Recommendation | TransferPlan | ConversationReply | RememberAck,
)
def ask(
    request: AskRequest, reasoning: Reasoning = Depends(get_reasoning)
) -> Recommendation | TransferPlan | ConversationReply | RememberAck:
    """Issue 27: one turn of a conversation, not a standalone question.

    The prior transcript is handed to the reasoning layer and this turn is
    appended to it, so "why not Haaland?" reaches a model that knows what it
    just said. The answer may now be a ConversationReply -- the model asking
    or arguing rather than finalising -- which is logged like any other turn
    but is not a decision, so nothing is written to recommendation_log or the
    roadmap for it.
    """
    remember_text = extract_remember_instruction(request.question)
    if remember_text is not None:
        if is_instinct(remember_text):
            with get_connection() as conn:
                log_instinct(conn, MY_TEAM_ID, remember_text)
        else:
            append_preference(remember_text)
        return RememberAck(remembered=remember_text)

    with get_connection() as conn:
        conversation = load_conversation(conn, MY_TEAM_ID)
        tools = _build_tools(conn)
        system = build_system_prompt(load_preferences())
        result = reasoning.reason(
            request.question,
            tools,
            system=system,
            validate=_make_validator(conn),
            history=conversation.turns,
        )

        # Issue 27: a revision becomes the plan it produces, and does so
        # BEFORE the transcript is written -- what the user was shown, and
        # what the model must see next turn, is the whole revised plan, not
        # the one step in isolation.
        #
        # The merge is repeated here rather than carried over from the
        # validator: the validator's job is to answer yes or no, and threading
        # its intermediate value out would make the legality gate and the
        # write path share mutable state. It is a pure list rebuild over a
        # handful of steps.
        note: str | None = None
        if isinstance(result, PlanRevision):
            live = get_live_roadmap(conn, MY_TEAM_ID)
            if live is None:
                # Only reachable if the roadmap was deleted between the
                # validator passing and here, since the validator refuses a
                # revision with nothing to revise.
                raise HTTPException(status_code=409, detail="No roadmap is stored to revise.")
            note = result.note
            result = merge_step(live.plan, result.gameweek, result.step)

        conversation.turns.extend(
            [user_turn(request.question), assistant_turn(result, note=note)]
        )
        save_conversation(conn, conversation)

        # A reply is not a decision: there is no pick to log against an
        # outcome (ADR-0012) and no plan to store as a roadmap (ADR-0016).
        if isinstance(result, ConversationReply):
            return result

        _log_result(conn, result)
        # Issue 25: a multi-week answer becomes a Roadmap the user is
        # following, not prose that evaporates when the response renders.
        # Stored on every plan rather than behind an explicit "save" because
        # versioning makes that safe -- a what-if question creates version 4,
        # it does not destroy version 3, and the check-in reads whichever is
        # live. An explicit save would mean the common case (asking for a
        # plan and then acting on it) silently stored nothing.
        if isinstance(result, TransferPlan):
            save_roadmap(conn, MY_TEAM_ID, result, prompt=request.question)
        return result


@router.get("/conversation", response_model=Conversation)
def conversation() -> Conversation:
    """Issue 27: the live transcript.

    A read (ADR-0015) -- no Claude call. Empty when nothing has been asked
    this gameweek, and automatically empty once the gameweek rolls over: an
    argument about a deadline that has passed is noise, not context.
    """
    with get_connection() as conn:
        return load_conversation(conn, MY_TEAM_ID)


@router.delete("/conversation", response_model=Conversation)
def reset_conversation() -> Conversation:
    """Start again. Deliberately available: a conversation that has wandered
    is cheaper to abandon than to argue back on course, and the alternative is
    the user pretending a fresh question is fresh when the model can still see
    the old one."""
    with get_connection() as conn:
        clear_conversation(conn, MY_TEAM_ID)
        return load_conversation(conn, MY_TEAM_ID)


@router.get("/team", response_model=Team)
def team() -> Team:
    with get_connection() as conn:
        return get_my_team(conn, MY_TEAM_ID)


@router.get("/team/instincts", response_model=Instincts)
def team_instincts() -> Instincts:
    with get_connection() as conn:
        return get_instincts(conn, MY_TEAM_ID)


@router.get("/team/consensus", response_model=list[CreatorConsensus])
def team_consensus() -> list[CreatorConsensus]:
    """Issue 19: creator consensus movement for the players the user has a
    live Instinct about -- ticket 13's "did my thinking evolve or did I get
    herded" question, with the missing half (what changed in the world)
    finally attached.

    Scoped to those players deliberately rather than offering a search box.
    The dashboard is the surface where the user is *reviewing their own
    decision*; a general consensus lookup on any player would turn the
    bandwagon meter into a way to go shopping for bandwagons, which is the
    opposite of the point. Ask about any other player in the Ask tab, where
    the reasoning layer will frame the count as a risk.

    Empty list when nothing is logged this gameweek -- the panel then renders
    nothing, same as InstinctPanel.

    Reads only: no Claude call and no FPL API call, so opening the dashboard
    stays free (ADR-0002's on-demand billing shape, ticket 15's cached
    suggestion aside).
    """
    with get_connection() as conn:
        return [
            get_creator_consensus(conn, player_name, MY_TEAM_ID)
            for player_name, _ in instinct_players(conn, MY_TEAM_ID)
        ]


@router.get("/team/roadmap", response_model=Roadmap | None)
def team_roadmap() -> Roadmap | None:
    """Issue 25: the multi-week plan the user is currently following.

    None when none has been stored. `status` distinguishes a roadmap still
    worth acting on from one that has simply run its course -- a month-old
    plan whose every step is in the past must not keep looking authoritative.
    """
    with get_connection() as conn:
        return get_live_roadmap(conn, MY_TEAM_ID)


@router.get("/team/roadmap/versions", response_model=list[Roadmap])
def team_roadmap_versions() -> list[Roadmap]:
    """Every version, newest first, superseded ones included.

    This is the reason roadmaps are versioned rather than overwritten:
    preferences.md asks to be able to say why a pick changed -- genuine
    reassessment or bandwagon influence -- and that is unanswerable against a
    row that was replaced in place.
    """
    with get_connection() as conn:
        return get_roadmap_versions(conn, MY_TEAM_ID)


@router.get("/team/roadmap/check-in", response_model=RoadmapCheckIn)
def team_roadmap_check_in() -> RoadmapCheckIn:
    """Issue 25: what changed since the roadmap was made.

    Reads only -- no Claude call and no live FPL API call (ADR-0015), so the
    dashboard can show whether the plan still holds without that costing
    anything. Revising the plan is a separate, deliberate act by the user.
    """
    with get_connection() as conn:
        return check_in(conn, MY_TEAM_ID)


@router.get("/creators/attention", response_model=AttentionBoard)
def creator_attention() -> AttentionBoard:
    """Issue 28: which players trusted creators are talking about, ranked by
    how much that moved rather than by how loud it is.

    Not under /team: this is about the discourse, not about the user's squad,
    and a player they do not own is exactly the interesting row.

    A read (ADR-0015) -- counting rows ingestion already wrote, so it costs
    nothing. And deliberately not a shortlist to buy from: ticket 19 kept
    consensus scoped to the user's own instinct-players so it could not become
    bandwagon shopping, so inverting that scoping means carrying the framing
    instead. preferences.md treats heavy coverage as a risk.
    """
    with get_connection() as conn:
        return build_attention_board(conn, MY_TEAM_ID)


@router.get("/creators/themes", response_model=ThemeBoard)
def creator_themes() -> ThemeBoard:
    """Issue 29: what creators are collectively talking about, clustered from
    the embeddings ticket 09 already paid for.

    A read (ADR-0015): pgvector does the distance work, and no LLM is involved
    at any point -- including the labels, which are real chunks of creator text
    rather than generated prose. A model-authored theme name would be the same
    hallucination surface ADR-0014 closed for citations.
    """
    with get_connection() as conn:
        return build_themes(conn, MY_TEAM_ID)


@router.get("/team/checklist", response_model=DeadlineChecklist)
def team_checklist() -> DeadlineChecklist:
    """Issue 23: the deadline screen -- countdown, checkable squad facts, and
    price movement on the user's own players.

    Deliberately NOT a reasoning call. The dashboard already carries one
    cached AI suggestion (ticket 15) and that is the billed thing on this
    screen; everything here is a deterministic read, so refreshing the page to
    watch the countdown costs nothing. It also makes no live FPL API call --
    the whole answer comes out of what the last fpl-ingest wrote, and the
    items say so themselves when that is stale or missing rather than
    silently reporting a clean bill of health.
    """
    with get_connection() as conn:
        return build_checklist(conn, MY_TEAM_ID)


# Only a SUCCESSFUL suggestion is persisted, so a failed one used to be
# regenerated on every single load of the dashboard -- each attempt a full
# reasoning loop of up to _MAX_TURNS billed calls. A page the user leaves open
# and refreshes could therefore bill indefinitely for an answer that is not
# arriving. This is deliberately in process memory rather than a table: it is
# a rate limit on a retry, not a fact about the team, and forgetting it on
# restart is the correct behaviour.
_SUGGESTION_FAILED_AT: dict[int, float] = {}
_SUGGESTION_RETRY_AFTER_S = 15 * 60


@router.get("/team/suggestion", response_model=Recommendation | TransferPlan | None)
def team_suggestion(
    reasoning: Reasoning = Depends(get_reasoning),
) -> Recommendation | TransferPlan | None:
    with get_connection() as conn:
        gameweek = planning_gameweek(conn, MY_TEAM_ID)
        if gameweek is None:
            # No ingestion has run yet -- nothing to base a suggestion on.
            return None

        cached = get_cached_suggestion(conn, MY_TEAM_ID, gameweek)
        if cached is not None:
            return cached

        failed_at = _SUGGESTION_FAILED_AT.get(gameweek)
        if failed_at is not None:
            if time.monotonic() - failed_at < _SUGGESTION_RETRY_AFTER_S:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The last attempt to generate this week's suggestion did "
                        "not converge. Ask a question directly, or try again in a "
                        "few minutes."
                    ),
                )
            del _SUGGESTION_FAILED_AT[gameweek]

        tools = _build_tools(conn)
        system = build_system_prompt(load_preferences())
        try:
            result = reasoning.reason(
                _SUGGESTION_PROMPT, tools, system=system, validate=_make_validator(conn)
            )
        except RuntimeError:
            _SUGGESTION_FAILED_AT[gameweek] = time.monotonic()
            _log.warning(
                "suggestion generation failed for gameweek %s; not retrying for %ds",
                gameweek,
                _SUGGESTION_RETRY_AFTER_S,
            )
            raise
        save_suggestion(conn, MY_TEAM_ID, gameweek, result)
        _log_result(conn, result)
        return result


# ---------------------------------------------------------------------------
# Refreshing the data.
#
# ADR-0002 chose on-demand ingestion over a scheduler, and these endpoints
# keep that choice -- nothing here runs unless the user presses something.
# What they change is the ceremony. A refresh was previously reachable only
# from a terminal, which for a weekly act is the wrong amount of friction in
# the wrong direction: too much to do casually on a Friday, and no record
# afterwards of whether it had been done at all.
#
# The two kinds are deliberately separate endpoints rather than one "refresh
# everything", because their costs are not comparable. `fpl` is ~100 requests
# to two public APIs and no model calls: free, and pressable as often as you
# like. `creators` bills Grok, Claude and Voyage per run. Collapsing them
# would mean the free refresh could only be had by paying for the other one,
# and the user would learn to avoid the button.


@router.get("/ingest/status", response_model=IngestionStatus)
def ingest_status() -> IngestionStatus:
    """What the refresh control shows: recent runs, per-source freshness, and
    whether anything is in flight.

    A read (ADR-0015), and it is also where a crashed run gets cleaned up --
    `ingestion_status` reaps stale claims. Recovery therefore rides on the
    user opening the page, which needs no scheduler of its own and is the
    only reliable trigger available.
    """
    with get_connection() as conn:
        return ingestion_status(conn, MY_TEAM_ID)


@router.post("/ingest/{kind}", response_model=IngestionRun, status_code=202)
def start_ingestion(kind: str, background: BackgroundTasks) -> IngestionRun:
    """Start a refresh and return immediately with the run to poll.

    202, not 200: an FPL run takes a couple of minutes and a creator run can
    take tens, so the work happens in a background task and the response is
    an acknowledgement rather than a result. Holding the request open for the
    duration would mean a refresh that fails whenever the browser gives up
    first -- and, worse, a run whose fate depends on whether the tab stayed
    open.

    409 when one is already in flight. That is enforced by a partial unique
    index rather than by checking first (runs.py), so two simultaneous
    requests cannot both win the check -- which for the billed pipeline is
    the difference between one invoice and two.
    """
    if kind not in KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown ingestion kind {kind!r}. Expected one of: {', '.join(KINDS)}.",
        )

    with get_connection() as conn:
        try:
            run = start_run(conn, MY_TEAM_ID, kind, trigger="api")
        except RunAlreadyRunning as exc:
            # The existing run is returned in the detail rather than just a
            # message: the UI can then show "started 4 minutes ago" and the
            # user has no reason to keep clicking.
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "run": exc.run.model_dump()},
            ) from None

    # Scheduled AFTER the connection above has closed and the claim is
    # committed. execute_run opens its own connection precisely because this
    # outlives the request.
    background.add_task(execute_run, run.id, kind, MY_TEAM_ID)
    return run
