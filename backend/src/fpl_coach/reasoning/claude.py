import logging
import os
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from fpl_coach.models import (
    Alternative,
    Citation,
    ConversationReply,
    ConversationTurn,
    CreatorOpinions,
    Evidence,
    PlanRevision,
    PlanStep,
    Player,
    PlayerComparison,
    PriceChangePredictions,
    Recommendation,
    RecommendationPick,
    SemanticCreatorOpinions,
    Team,
    TransferComparison,
    TransferPlan,
)
from fpl_coach.conversation import render_history
from fpl_coach.reasoning.base import ResultValidator, ToolSpec

# Issue 12: bumped from 10. A small manual comparison (not a rigorous
# sample -- a handful of runs each way) suggested offering submit_plan
# alongside submit_recommendation every turn costs a few extra turns of
# deliberation on an ordinary single-gameweek question, occasionally enough
# to blow the old budget. 16 is a judgment call, not a measured optimum --
# revisit if turn-budget RuntimeErrors keep showing up in practice. Note
# this does NOT fix a genuine stuck loop (e.g. a tool call Claude keeps
# retrying with no exit condition) -- it only buys headroom for legitimate
# multi-tool-call flows, at the cost of a slower, more expensive failure
# when a conversation is actually stuck.
_MAX_TURNS = 16
# Issue 12: a submit_plan call carries several steps' worth of evidence/risk
# arrays -- 1024 was tuned for a single submit_recommendation call and can
# truncate mid-tool_use on a 3+ step plan.
_MAX_TOKENS = 4096
# A conversation that exhausts _MAX_TURNS, or a turn where Claude replies
# with no tool_use at all, is real-Claude nondeterminism observed in
# practice (see docs/agents' notes on this project) -- the same question
# normally converges on a second, fresh attempt. One retry with a brand-new
# conversation (not a continuation of the stuck one) turns an occasional
# hard failure into a slower response instead of a 500 the user has to
# manually retry themselves. This does not paper over a genuinely stuck
# tool-call loop within a single attempt -- _MAX_TURNS still bounds that.
_MAX_ATTEMPTS = 2


class TurnBudgetExhausted(RuntimeError):
    """The loop used all _MAX_TURNS and never reached a terminal tool.

    A distinct type so it can be logged distinctly -- NOT so it can skip the
    retry. Skipping the retry was tried (2026-09-18) on the reasoning that a
    sixteen-turn failure should not be allowed to become a thirty-two-turn
    one, and it was wrong: this failure is the ordinary nondeterminism the
    retry was built for, it usually converges on the second attempt, and
    without the retry the user gets "couldn't reach the coach" instead of a
    slower answer. A hard failure is not cheaper than a retry -- the question
    still has to be asked again, by hand, and the first attempt is billed
    either way.
    """

_log = logging.getLogger(__name__)

# Per-MTok USD, input/output. Only used to turn the token counts below into a
# number worth reacting to -- the token counts are the measurement, this is
# arithmetic on top. Confirm against platform.claude.com/pricing before
# trusting a figure; a stale rate here misreports the bill, it does not change
# it. A model missing from the table logs tokens and no dollars.
_RATES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


def _turn_cost(model: str, usage: Any) -> float | None:
    """What one `messages.create` actually billed.

    Four token classes at four different prices -- the whole point of the
    cache markers in `_reason_once` is to move tokens from the first bucket
    to the third, so a log line that collapsed them into one "input" figure
    could not show whether the caching works. Cache writes bill at 1.25x
    input for the 5-minute TTL, reads at 0.1x.
    """
    rate = _RATES.get(model)
    if rate is None:
        return None
    per_in, per_out = rate
    return (
        getattr(usage, "input_tokens", 0) * per_in
        + getattr(usage, "cache_creation_input_tokens", 0) * per_in * 1.25
        + getattr(usage, "cache_read_input_tokens", 0) * per_in * 0.1
        + getattr(usage, "output_tokens", 0) * per_out
    ) / 1_000_000


@dataclass(frozen=True)
class _KnownPlayer:
    """Common id/name shape for anything a tool result can make a valid
    submit_recommendation pick — a full Player, or just a player_id/name
    pair from a lighter-weight result like PriceChangePredictions."""

    id: int
    name: str


class ClaudeReasoning:
    """ADR-0003's Claude-backed Reasoning implementation.

    ADR-0004: the primary pick must be deterministic, not sampled prose.
    Rather than parsing a player name out of the model's text, the model is
    forced to finalize through a `submit_recommendation` tool that names a
    player by id, and the returned Recommendation's pick is always built by
    looking that id up in `known_players` — the squad (from `get_my_team`,
    seeded up front) plus whatever `get_player`/`compare_players` results
    came back during the conversation (issue 05: the pick may be a player
    outside the squad, e.g. "is Bukayo Saka worth buying?", but only once
    the model has looked them up). Creativity stays confined to confidence,
    evidence, risks, and the alternative's reasoning.

    That id used to also be constrained by a JSON Schema enum of exactly
    those players. The enum was removed for cost, not for taste: it made the
    tool schema a function of conversation state, and tool schemas render at
    the very front of the cached prefix, so each newly-known player dropped
    the system prompt and the entire transcript out of cache mid-question.
    What the enum actually guaranteed is unchanged — an id outside
    `known_players` raises KeyError in `_build_recommendation` and comes back
    to the model as an is_error tool_result telling it to look the player up
    first, through the same channel ticket 22 uses for an illegal move. The
    model gets a turn to correct itself instead of the prefix being rebuilt
    on every turn to make the mistake unrepresentable.

    Issue 17 applies the same rule to sources. Creator-opinion tool results
    carry a `citation_id`; an evidence line cites one by id and the
    attribution (creator, trust tier, url, timestamp) is looked up from the
    tool result that produced it. The model chooses *which* source backs a
    line — it never writes the link, because a model-authored URL is a
    hallucinated URL and a citation the user cannot check is worse than none.
    """

    def __init__(self, model: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model or os.environ.get("REASONING_MODEL", "claude-sonnet-5")
        # Left unset, Opus 5 reasons at effort "high" on every one of up to
        # _MAX_TURNS calls, and thinking tokens bill at the output rate. FPL
        # advice is knowledge work rather than long-horizon coding, which is
        # the shape where Anthropic's published sweeps find "medium" matching
        # the default's accuracy at 70-85% of its cost. Env-settable because
        # that figure is a starting point for this workload, not a result:
        # sweep it with `pytest -m integration` and the per-turn cost the loop
        # now logs before trusting any particular level.
        self._effort = os.environ.get("REASONING_EFFORT", "medium")

    def reason(
        self,
        prompt: str,
        tools: list[ToolSpec],
        system: str = "",
        validate: ResultValidator | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> Recommendation | TransferPlan | ConversationReply | PlanRevision:
        """Issue 27: retry is per-TURN, never per-conversation.

        _MAX_ATTEMPTS used to restart a stuck exchange with a brand-new
        message list, which was fine when the exchange was one question. With
        a durable transcript "from scratch" would mean discarding the user's
        own earlier messages, so what is retried is this turn against the same
        history -- the conversation is state, and state is not rebuilt.
        """
        last_exc: RuntimeError | None = None
        for _ in range(_MAX_ATTEMPTS):
            try:
                return self._reason_once(prompt, tools, system, validate, history)
            except RuntimeError as exc:
                # Logged at WARNING because a retried attempt is billed in
                # full and then thrown away: the one event where the bill
                # moves and there is nothing to show the user for it.
                _log.warning("reasoning attempt failed, retrying: %s", exc)
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def _reason_once(
        self,
        prompt: str,
        tools: list[ToolSpec],
        system: str,
        validate: ResultValidator | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> Recommendation | TransferPlan | ConversationReply:
        get_my_team = next(t for t in tools if t.name == "get_my_team")
        known_players: dict[int, _KnownPlayer] = {
            p.id: _KnownPlayer(id=p.id, name=p.name) for p in get_my_team.handler().players
        }
        # Issue 17: starts empty, unlike known_players, because nothing is
        # citable until a creator tool has actually returned something. It is
        # the authority for what a citation_id may resolve to, but no longer
        # shapes the schema that carries one -- see _evidence_schema.
        known_citations: dict[int, Citation] = {}

        # Prompt caching: these _MAX_TURNS calls replay the same tool schemas,
        # the same system prompt, and a monotonically growing message history
        # on every turn -- so without caching a question that runs n turns pays
        # for its own history about n^2/2 times.
        #
        # The rule that makes it work is that the request renders in the order
        # tools -> system -> messages, and a cache entry is a PREFIX match: one
        # changed byte drops everything after it. Putting the volatile submit_*
        # tools last within `tools` is therefore not enough, which is what this
        # code used to do -- `system` and the entire message history render
        # AFTER every tool, so a submit schema that grew a player id mid-
        # conversation dropped the system prompt and the whole transcript out
        # of cache and re-billed them at full price plus a 1.25x write premium.
        # It fired on exactly the turns that cost most: the long ones, after
        # several lookups.
        #
        # So every tool schema here is now byte-identical for the life of a
        # conversation (see _evidence_schema and _build_respond_tool for the
        # two that were not), and the constraints those schemas used to encode
        # are enforced where they always really lived -- in the builders below,
        # against known_players and known_citations.
        #
        # Three breakpoints, coarse to fine: the domain tools never vary at all
        # (so they cache across different questions entirely), `system` varies
        # only when preferences.md is edited, and top-level automatic caching
        # walks the growing history back to the nearest prior write so each
        # turn pays full price for the newest tool_result alone.
        anthropic_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        if anthropic_tools:
            anthropic_tools[-1] = {
                **anthropic_tools[-1],
                "cache_control": {"type": "ephemeral"},
            }
        handlers = {t.name: t.handler for t in tools}

        # Issue 27: the prior transcript, rendered as plain text messages. Not
        # the earlier turns' tool calls -- see conversation.render_history for
        # why working is not replayed, and why a rendered turn cannot smuggle
        # a player id past ADR-0004's enum.
        messages: list[dict[str, Any]] = [
            *render_history(history or []),
            {"role": "user", "content": prompt},
        ]
        system_kwargs = (
            {
                "system": [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            }
            if system
            else {}
        )

        # Issue 27: the escape hatch that must not exist.
        #
        # Tickets 22 and 25 both work by REFUSING a submit and handing the
        # model an is_error tool_result to revise against. That mechanism
        # depended on the submit tools being the only way out of this loop.
        # Adding respond_to_user creates a third exit -- so told "this
        # transfer is illegal", the model could answer in prose instead of
        # fixing it, and the user would get a fluent reply where they should
        # have got a corrected recommendation.
        #
        # Once a validator has refused something, the next call has to be a
        # submit. The latch is never cleared: within this turn there is no
        # legitimate reason to stop and chat after being told the move is
        # impossible -- if nothing legal is worth doing, 'roll' says so.
        must_submit = False

        # One question is a loop of up to _MAX_TURNS billed calls, and until
        # now nothing recorded that -- the only visible number was the monthly
        # bill, which cannot say whether it came from many cheap questions or
        # a few runaway ones. Totals are logged on every exit path below,
        # including the failure paths: a question that burns 16 turns and then
        # raises is the expensive case, so it is the one that must not be
        # silent.
        spent = 0.0
        turns = 0

        def log_total(outcome: str) -> None:
            _log.info(
                "reasoning attempt finished (%s): %d turn(s)%s",
                outcome,
                turns,
                f", ${spent:.4f}" if spent else "",
            )

        for _ in range(_MAX_TURNS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                # Automatic caching (top-level cache_control) covers the growing
                # messages history -- it walks back from the last block to the
                # nearest prior write, so each turn only pays full price for the
                # newest tool_result, not the whole conversation replayed so far.
                cache_control={"type": "ephemeral"},
                tools=anthropic_tools
                + [
                    self._build_submit_tool(),
                    self._build_submit_plan_tool(),
                    self._build_revise_step_tool(),
                    self._build_respond_tool(),
                ],
                tool_choice={"type": "any"},
                # Pinned per route, never varied mid-conversation: an effort
                # change invalidates the messages cache, so a request that
                # moved it between turns would undo the caching above.
                output_config={"effort": self._effort},
                messages=messages,
                **system_kwargs,
            )
            messages.append({"role": "assistant", "content": response.content})

            turns += 1
            usage = response.usage
            cost = _turn_cost(self._model, usage)
            spent += cost or 0.0
            # cache_read dominating input_tokens is the healthy signature; a
            # cache_creation figure near the whole conversation on every turn
            # means something above the breakpoint is moving between turns.
            _log.info(
                "reasoning turn %d: in=%d cache_write=%d cache_read=%d out=%d%s",
                turns,
                usage.input_tokens,
                usage.cache_creation_input_tokens or 0,
                usage.cache_read_input_tokens or 0,
                usage.output_tokens,
                f" ${cost:.4f}" if cost is not None else "",
            )

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                log_total("no tool_use")
                raise RuntimeError(
                    "Claude finished without calling submit_recommendation or submit_plan"
                )

            tool_results = []
            submitted = None
            for block in tool_uses:
                if block.name == "respond_to_user":
                    if must_submit:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": (
                                    "You cannot reply in prose here. A move you "
                                    "submitted was refused as illegal, and the user "
                                    "needs a corrected decision rather than an "
                                    "explanation. Call submit_recommendation, "
                                    "submit_plan or revise_plan_step -- if no legal "
                                    "move is worth making, submit decision 'roll' "
                                    "with no pick."
                                ),
                                "is_error": True,
                            }
                        )
                        continue
                    text = (block.input or {}).get("text")
                    if not text:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "respond_to_user needs a non-empty `text`.",
                                "is_error": True,
                            }
                        )
                        continue
                    log_total("reply")
                    return ConversationReply(text=text)

                if block.name in (
                    "submit_recommendation",
                    "submit_plan",
                    "revise_plan_step",
                ):
                    # The model API is a boundary: an occasional malformed
                    # tool_use (seen in practice -- garbled args, a missing
                    # required key) must not crash the whole request when
                    # Claude could simply be told to retry with well-formed
                    # arguments, same as any other tool call failing below.
                    try:
                        if block.name == "submit_recommendation":
                            submitted = self._build_recommendation(
                                block.input, known_players, known_citations
                            )
                        elif block.name == "submit_plan":
                            submitted = self._build_plan(
                                block.input, known_players, known_citations
                            )
                        else:
                            submitted = self._build_revision(
                                block.input, known_players, known_citations
                            )
                    except (KeyError, TypeError, ValidationError) as exc:
                        # A KeyError here is nearly always an unknown player
                        # id, which since the enum came off the schema is a
                        # mistake the model can now make -- so the message has
                        # to tell it the way out, not just name the exception.
                        hint = (
                            " That player id has not appeared in any tool result "
                            "in this conversation. Call get_player or "
                            "compare_players to look the player up first, then "
                            "submit again -- or pick a player you have data for."
                            if isinstance(exc, KeyError)
                            else ""
                        )
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"Invalid {block.name} arguments: {exc}.{hint}",
                                "is_error": True,
                            }
                        )
                        continue

                    # Issue 22: the legality gate on the finalisation path.
                    # A refusal is bounced back through the same is_error
                    # channel as a malformed submit, so the model gets the
                    # violation and another turn to revise rather than the
                    # user getting an illegal move. `submitted` stays None,
                    # so the loop continues.
                    problems = validate(submitted) if validate is not None else []
                    if problems:
                        submitted = None
                        must_submit = True
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": (
                                    "This move is not legal in FPL and cannot be "
                                    "submitted: "
                                    + " ".join(problems)
                                    + " Revise and submit a legal decision -- if there "
                                    "is no legal move worth making, submit decision "
                                    "'roll' with no pick."
                                ),
                                "is_error": True,
                            }
                        )
                    continue

                try:
                    result = handlers[block.name](**block.input)
                except Exception as exc:  # noqa: BLE001 - surfaced to Claude, not raised
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(exc),
                            "is_error": True,
                        }
                    )
                    continue

                self._record_known_players(result, known_players)
                self._record_known_citations(result, known_citations)
                content = (
                    result.model_dump_json() if isinstance(result, BaseModel) else str(result)
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content}
                )

            if submitted is not None:
                log_total("submitted")
                return submitted

            # The enum on the submit schemas did two jobs: it made an
            # unlooked-up pick unrepresentable, AND it told the model which
            # ids existed. Removing it for cache stability kept the first job
            # (the builders still refuse an unknown id) and dropped the
            # second, which is how a model ends up guessing and getting
            # bounced until the turn budget runs out.
            #
            # So the roster goes in the message stream instead. This is the
            # documented place for a per-turn reminder: a text block after the
            # tool_result blocks, in the same user message. Messages are
            # append-only here, so unlike the schema it costs no cache -- the
            # reminder lands after the last breakpoint and the prefix in front
            # of it is untouched.
            pickable = ", ".join(
                f"{p.id} ({p.name})" for p in sorted(known_players.values(), key=lambda p: p.name)
            )
            reminder = (
                "Valid player ids for a submit right now: " + pickable + ". "
                "Any other id is refused -- call get_player or compare_players "
                "to add a player to this list first."
            )
            if known_citations:
                reminder += (
                    " Citable citation_ids: "
                    + ", ".join(str(cid) for cid in sorted(known_citations))
                    + "."
                )
            messages.append(
                {
                    "role": "user",
                    "content": [*tool_results, {"type": "text", "text": reminder}],
                }
            )

        log_total("turn budget exhausted")
        raise TurnBudgetExhausted(
            "Claude did not call submit_recommendation within the turn budget"
        )

    @staticmethod
    def _record_known_players(result: Any, known_players: dict[int, _KnownPlayer]) -> None:
        if isinstance(result, Player):
            known_players[result.id] = _KnownPlayer(id=result.id, name=result.name)
        elif isinstance(result, (Team, PlayerComparison)):
            for p in result.players:
                known_players[p.id] = _KnownPlayer(id=p.id, name=p.name)
        elif isinstance(result, PriceChangePredictions):
            for p in result.rising + result.falling:
                known_players[p.player_id] = _KnownPlayer(id=p.player_id, name=p.player_name)
        elif isinstance(result, TransferComparison):
            for option in (result.player_out, result.player_in):
                known_players[option.player.id] = _KnownPlayer(
                    id=option.player.id, name=option.player.name
                )

    @staticmethod
    def _record_known_citations(result: Any, known_citations: dict[int, Citation]) -> None:
        """Issue 17's mirror of _record_known_players: only a Stance the model
        has actually been shown can be cited, and the citation is built here
        from the tool result rather than later from the model's own words."""
        views: list = []
        if isinstance(result, CreatorOpinions):
            views = list(result.stances)
        elif isinstance(result, SemanticCreatorOpinions):
            views = list(result.matches)
        elif isinstance(result, TransferComparison):
            # Issue 11's recommend_transfers embeds get_creator_opinions'
            # stances in each side of the comparison, so a transfer question
            # gets citable creator opinion without a second tool call.
            views = list(result.player_out.creator_stances) + list(
                result.player_in.creator_stances
            )

        for view in views:
            if view.citation_id is None:
                continue
            citation = Citation.from_stance(view)
            if citation is not None:
                known_citations[view.citation_id] = citation

    @staticmethod
    def _evidence_schema() -> dict:
        """The evidence item shape -- byte-identical on every turn of a
        conversation, which is the whole point.

        citation_id used to be omitted until a creator tool had returned
        something citable, and then carried an enum of the ids seen so far.
        Both of those made this schema a function of conversation state, and
        the schema renders into the cached prefix ahead of the system prompt
        and the whole message history -- so every new citable stance moved
        bytes near the front of the prefix and dropped everything after it
        out of cache, at full price plus a fresh write premium.

        The guarantee that mattered is unchanged: `_build_evidence` looks the
        id up in `known_citations` and drops one it does not recognise, so a
        line the model invented an id for goes out unattributed rather than
        wrongly attributed. The model is told the rule in prose instead of
        having it enforced by JSON Schema -- and a rule it can read is worth
        more than a cache it keeps invalidating.
        """
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "one evidence line, in your own words",
                    },
                    "citation_id": {
                        "type": "integer",
                        "description": (
                            "citation_id of the creator stance this line is based on, "
                            "exactly as returned by get_creator_opinions, "
                            "search_creator_opinions, or recommend_transfers. Include "
                            "it whenever the line rests on what a creator said, so the "
                            "user can click through to the source. Omit it for a line "
                            "that rests on stats or fixtures instead -- an unsourced "
                            "line is fine, a line attributed to the wrong source is "
                            "not. Use ONLY an id that appeared in a tool result in "
                            "this conversation: never invent one, and never write a "
                            "URL yourself. An unrecognised id is silently dropped and "
                            "your line loses its source."
                        ),
                    },
                },
                "required": ["text"],
            },
        }

    @staticmethod
    def _build_evidence(items: Any, known_citations: dict[int, Citation]) -> list[Evidence]:
        """Strict about the container, forgiving about an item.

        A non-list `evidence` is a malformed tool call and gets bounced back
        to the model, because iterating a bare string would silently produce
        one evidence line per character. A bare string *item* is merely an
        uncited line: turning that model slip into a degraded-but-honest
        answer beats failing the whole request over formatting.
        """
        if isinstance(items, str) or not isinstance(items, list):
            raise TypeError(f"evidence must be a list, got {type(items).__name__}")

        evidence = []
        for item in items:
            if isinstance(item, str):
                evidence.append(Evidence(text=item))
                continue
            # An id the model invented, or one from a conversation this
            # attempt never saw, is dropped rather than guessed at. The prose
            # still stands; it just goes out unattributed.
            evidence.append(
                Evidence(
                    text=item["text"],
                    citation=known_citations.get(item.get("citation_id")),
                )
            )
        return evidence

    @staticmethod
    def _build_respond_tool() -> dict:
        """Issue 27: the terminal that lets the model talk instead of decide.

        Takes no arguments on purpose. It used to have "DISABLED right now"
        appended to its description while the must_submit latch was set, which
        rewrote a tool definition in the middle of a conversation -- and a tool
        definition is the very front of the cached prefix. The latch is still
        enforced, in the loop, by refusing the call with an is_error
        tool_result that says the same thing in the same turn. Same behaviour,
        without moving the prefix out from under the cache at the exact moment
        the conversation is longest and a miss costs most.

        It carries no player id, no evidence and no confidence, deliberately.
        ADR-0004 says a pick is built from an authoritative lookup rather than
        parsed from prose, and the way to keep that true while allowing
        conversation is to make the conversational exit structurally incapable
        of naming a decision. Prose stays prose; a decision still only ever
        comes from a submit tool against a real enum.
        """
        description = "\n\n".join(
            [
                "Reply to the user in your own words INSTEAD of finalizing, when "
                "answering is not yet the right move: to ask which of two players "
                "they meant, to engage with an objection they raised, or to explain "
                "your reasoning when they have challenged it.",
                # The instruction that keeps this from turning the coach into a
                # mirror. fpl_rules.py already says to give the honest assessment
                # "even when it contradicts what the manager seems to want", and
                # ticket 11's PUSHBACK: exists so the app can disagree.
                "Use this to reconsider, not to capitulate. If the evidence still "
                "supports what you said, hold that position and say why -- agreeing "
                "because the user pushed twice is worse than disagreeing. If they "
                "have given you a genuinely new fact, weigh it and change your mind "
                "openly.",
                "Do NOT use this to avoid a decision the user actually asked for. "
                "When they want a pick, call submit_recommendation.",
            ]
        )
        return {
            "name": "respond_to_user",
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "what to say to the user, in your own words",
                    }
                },
                "required": ["text"],
            },
        }

    @staticmethod
    def _build_submit_tool() -> dict:
        return {
            "name": "submit_recommendation",
            "description": (
                "Finalize the Recommendation for a single-gameweek question (one "
                "captain/transfer/roll/chip call). For a longer-horizon question (a "
                "multi-week or season-long plan), call submit_plan instead. Only "
                "players already returned by get_my_team, get_player, compare_players, "
                "predict_price_changes, or recommend_transfers are valid picks."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["captain", "transfer", "roll", "chip", "other"],
                        "description": (
                            "what this recommendation actually decides. 'captain' = who "
                            "to captain. 'transfer' = make a specific transfer. 'roll' = "
                            "deliberately make NO transfer this week (bank the free "
                            "transfer) -- use this whenever the right answer is to do "
                            "nothing, and omit pick_player_id entirely. 'chip' = play a "
                            "chip. 'other' = anything else."
                        ),
                    },
                    "pick_player_id": {
                        "type": "integer",
                        "description": (
                            "id of the player this decision names -- the captain for "
                            "'captain', the player coming IN for 'transfer'. OMIT this "
                            "entirely when the decision names no player, which is always "
                            "the case for 'roll'. Never pass a player just to fill the "
                            "field: a player id here is displayed to the user as the "
                            "answer, so a filler id reads as a recommendation you did "
                            "not make."
                        ),
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": ClaudeReasoning._evidence_schema(),
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "alternative_player_id": {
                        "type": "integer",
                        "description": (
                            "id of a genuine second-best option, if there is one. Omit "
                            "when there is no real alternative -- do not repeat "
                            "pick_player_id and do not pick an unrelated player to fill "
                            "this in."
                        ),
                    },
                    "alternative_reason": {
                        "type": "string",
                        "description": "why the alternative is the runner-up; omit if no alternative",
                    },
                },
                "required": ["decision", "confidence", "evidence", "risks"],
            },
        }

    @staticmethod
    def _build_submit_plan_tool() -> dict:
        return {
            "name": "submit_plan",
            "description": (
                "Finalize a multi-gameweek transfer plan for a longer-horizon question "
                "(multi-week or season-long) instead of submit_recommendation. Anchor "
                "each step's gameweek to get_my_team's current_gameweek. Each step "
                "needs a player id already returned by get_my_team, get_player, "
                "compare_players, predict_price_changes, or recommend_transfers. At "
                "least two steps.\n\n"
                "EVERY 'transfer' step must name out_player_id as well as "
                "pick_player_id. The plan is checked step by step against a running "
                "squad, bank and free-transfer count, and without knowing who is sold "
                "the budget for every later step is unknowable -- a transfer step "
                "missing out_player_id is refused, not accepted with a caveat. Steps "
                "may share a gameweek (a free transfer plus a hit) and need not be "
                "submitted in gameweek order."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "horizon": {
                        "type": "string",
                        "description": (
                            "brief label for the horizon inferred from the question, "
                            "e.g. 'multi-week' or 'season-long'"
                        ),
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "steps": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "gameweek": {"type": "integer"},
                                "decision": {
                                    "type": "string",
                                    "enum": ["captain", "transfer", "roll", "chip", "other"],
                                    "description": (
                                        "what this step does; 'roll' means bank the free "
                                        "transfer that week and omit pick_player_id"
                                    ),
                                },
                                "pick_player_id": {
                                    "type": "integer",
                                    "description": (
                                        "player this step names; for a 'transfer' step "
                                        "this is the player coming IN. Omit entirely "
                                        "for 'roll' or any step that names no player"
                                    ),
                                },
                                # Issue 25: same recorded-id enum as the pick, so a
                                # plan can never sell a player the model invented --
                                # ADR-0004's rule applied to the other side of the
                                # transfer. Squad membership is checked by the plan
                                # validator against the squad AT THAT STEP, which the
                                # enum alone cannot express: a player sold in GW5 is a
                                # legal id and an illegal sale in GW7.
                                "out_player_id": {
                                    "type": "integer",
                                    "description": (
                                        "the squad player SOLD in this step. Required "
                                        "for every 'transfer' step; omit for any other "
                                        "decision"
                                    ),
                                },
                                "evidence": ClaudeReasoning._evidence_schema(),
                                "risks": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["gameweek", "decision", "evidence", "risks"],
                        },
                    },
                },
                "required": ["horizon", "confidence", "steps"],
            },
        }

    @staticmethod
    def _build_recommendation(
        args: dict[str, Any],
        known_players: dict[int, _KnownPlayer],
        known_citations: dict[int, Citation],
    ) -> Recommendation:
        pick = None
        pick_id = args.get("pick_player_id")
        if pick_id is not None:
            pick_player = known_players[pick_id]
            pick = RecommendationPick(player_id=pick_player.id, player_name=pick_player.name)

        # An alternative is dropped rather than substituted when it is absent,
        # unusable, or the same player as the pick. Previously a duplicate id
        # was silently swapped for "any other known player", which put a
        # player the model never argued for in front of the user as the ALT.
        alternative = None
        alt_id = args.get("alternative_player_id")
        alt_reason = args.get("alternative_reason")
        if alt_id is not None and alt_reason and alt_id in known_players:
            if pick is None or alt_id != pick.player_id:
                alt_player = known_players[alt_id]
                alternative = Alternative(
                    player_id=alt_player.id,
                    player_name=alt_player.name,
                    reason=alt_reason,
                )

        return Recommendation(
            decision=args["decision"],
            pick=pick,
            confidence=args["confidence"],
            evidence=ClaudeReasoning._build_evidence(args["evidence"], known_citations),
            risks=args["risks"],
            alternative=alternative,
        )

    @staticmethod
    def _build_plan(
        args: dict[str, Any],
        known_players: dict[int, _KnownPlayer],
        known_citations: dict[int, Citation],
    ) -> TransferPlan:
        return TransferPlan(
            horizon=args["horizon"],
            confidence=args["confidence"],
            steps=[
                PlanStep(
                    gameweek=step["gameweek"],
                    decision=step["decision"],
                    pick=_step_pick(step, known_players, "pick_player_id"),
                    player_out=_step_pick(step, known_players, "out_player_id"),
                    evidence=ClaudeReasoning._build_evidence(
                        step["evidence"], known_citations
                    ),
                    risks=step["risks"],
                )
                for step in args["steps"]
            ],
        )

    @staticmethod
    def _build_revision(
        args: dict[str, Any],
        known_players: dict[int, _KnownPlayer],
        known_citations: dict[int, Citation],
    ) -> PlanRevision:
        """Issue 27: one revised step, built exactly like a `submit_plan` step.

        Sharing `_step_pick` and `_build_evidence` is the point rather than a
        convenience -- a revised step goes into the same stored plan as a
        submitted one, so it has to be unable to name a player or a citation
        the model was never shown, by the same mechanism.
        """
        step = args["step"]
        return PlanRevision(
            gameweek=args["gameweek"],
            note=args["note"],
            step=PlanStep(
                gameweek=args["gameweek"],
                decision=step["decision"],
                pick=_step_pick(step, known_players, "pick_player_id"),
                player_out=_step_pick(step, known_players, "out_player_id"),
                evidence=ClaudeReasoning._build_evidence(step["evidence"], known_citations),
                risks=step["risks"],
            ),
        )

    @staticmethod
    def _build_revise_step_tool() -> dict:
        return {
            "name": "revise_plan_step",
            "description": (
                "Change ONE step of the roadmap the user is already following, keeping "
                "every other step exactly as it is. Call get_roadmap first -- you can "
                "only revise a step that exists, and you name it by its gameweek. "
                "Use this, not submit_plan, when the user questions one week of the "
                "plan ('why am I rolling in GW5?', 'swap the GW6 transfer'). Rebuilding "
                "the whole plan throws away the reasoning behind the steps they did not "
                "ask about and silently changes steps they were happy with. "
                "Use submit_plan instead when the question genuinely invalidates more "
                "than one step, when no plan is stored yet, or when the gameweek has "
                "two steps and it is ambiguous which one is meant. The revised plan is "
                "re-checked end to end, so a change that makes a LATER step unaffordable "
                "is refused -- expect that and revise accordingly."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "gameweek": {
                        "type": "integer",
                        "description": (
                            "the gameweek of the existing step being replaced, exactly "
                            "as get_roadmap reported it"
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "one or two sentences for the user: what changed about this "
                            "step and why their question changed your mind (or, if it "
                            "did not, say the step stands and why)"
                        ),
                    },
                    "step": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["captain", "transfer", "roll", "chip", "other"],
                                "description": (
                                    "what this step now does; 'roll' means bank the free "
                                    "transfer and omit pick_player_id"
                                ),
                            },
                            "pick_player_id": {
                                "type": "integer",
                                "description": (
                                    "player this step names; for a 'transfer' step the "
                                    "player coming IN. Omit for 'roll'"
                                ),
                            },
                            "out_player_id": {
                                "type": "integer",
                                "description": (
                                    "the squad player SOLD in this step. Required for "
                                    "every 'transfer' step; omit otherwise"
                                ),
                            },
                            "evidence": ClaudeReasoning._evidence_schema(),
                            "risks": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["decision", "evidence", "risks"],
                    },
                },
                "required": ["gameweek", "note", "step"],
            },
        }


def _step_pick(
    step: dict[str, Any],
    known_players: dict[int, _KnownPlayer],
    key: str = "pick_player_id",
) -> RecommendationPick | None:
    """None when a plan step names no player for this slot.

    Two slots share this: the player coming IN (`pick_player_id`) and, since
    issue 25, the player going OUT (`out_player_id`). Both resolve through
    `known_players` rather than from anything the model wrote, so neither side
    of a transfer can be a name the model invented (ADR-0004). A 'roll' week
    has neither; a legal transfer step has both, and the plan validator
    refuses one that does not.
    """
    pick_id = step.get(key)
    if pick_id is None or pick_id not in known_players:
        return None
    player = known_players[pick_id]
    return RecommendationPick(player_id=player.id, player_name=player.name)
