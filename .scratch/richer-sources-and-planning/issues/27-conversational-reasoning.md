# 27 — Conversational reasoning: recommendations you can argue with

**What to build:** Make a reasoning exchange multi-turn, so the user can challenge a Recommendation or a roadmap step and get a real reconsideration rather than a fresh, context-free answer.

**Blocked by:** —

**Status:** ready-for-human

## Why

Every request to `/recommendations` is a brand-new conversation. `ClaudeReasoning.reason` builds `messages` from a single user prompt, loops up to 16 turns of tool calls, and the only exits are `submit_recommendation` or `submit_plan`. With `tool_choice: {"type": "any"}` the model *must* call a tool on every turn, and since the only terminals are the two submit tools, it cannot say "hold on, which of these did you mean?" or "that's a fair point, let me reconsider". It can only answer.

That produces the specific feeling of being talked at rather than advised ([[project_conversational_reasoning_idea]]). It also undermines the ticket 25 roadmap directly: a five-week plan the user cannot interrogate step by step is one they will not follow. And it sits badly against `preferences.md`:

> The final call should be justifiable as the user's own reasoning, not "because someone said so."

You cannot own a decision you were unable to question.

## Open decisions for triage

1. **Where conversation state lives.** ADR-0002 is on-demand with no scheduler and `MY_TEAM_ID` is a constant; there is no session concept anywhere in the app. Adding one touches the API shape, the front end, and possibly the schema. This is the real cost of the ticket and should be sized before committing.
2. **How to keep ADR-0004's guarantee.** The primary pick must stay deterministic — built from an authoritative lookup, never parsed from prose. A conversational mode must not become "the model writes an answer in text", or the guarantee that made this app trustworthy is gone. Likely shape: a `respond_to_user` terminal alongside the submit tools, so a clarifying reply is a *tool call* like any other and the pick still comes from `submit_recommendation` when one is finally made.
3. **What a challenge should be allowed to change.** A user pushing back should get genuine reconsideration, but not capitulation — `fpl_rules.py` already says to give the honest assessment "even when it contradicts what the manager seems to want", and ticket 11's `PUSHBACK:` exists precisely so the app can disagree. A conversational mode that folds under repetition is worse than one that cannot converse at all.
4. **Interaction with `known_players`.** The player-id enum grows as tools return results within one attempt. Across turns of a longer conversation this set becomes large, and the prompt-caching layout in `claude.py` deliberately excludes the submit tools from the cached prefix *because* that enum changes. A longer conversation makes that more expensive, not less ([[project_prompt_caching_reasoning]]).
5. **Retry semantics.** `_MAX_ATTEMPTS = 2` currently retries a stuck conversation from scratch. In a multi-turn session, "from scratch" is no longer well defined.

## Acceptance criteria (draft — confirm at triage)

- [x] The user can challenge a Recommendation and get a response that engages with the specific objection
- [x] The model can ask a clarifying question instead of being forced to answer, without breaking the forced-tool-call design
- [x] A pick is still built from an authoritative lookup, never from model-authored prose — ADR-0004 holds unchanged
- [x] The model holds its position when the evidence still supports it, and says why, rather than conceding to repetition
- [x] A roadmap step (ticket 25) can be questioned individually and revised without regenerating the whole plan
- [x] Turn budget and retry behaviour are redefined coherently for multi-turn sessions
- [ ] Token cost of a typical challenged exchange is measured against a single-shot one and recorded

## Triage — 2026-08-30

Triaged to `ready-for-agent`. The user's framing: a conversation is better than one question and one answer. Agreed, and the Ask tab is currently the weaker thing — `AskForm` is one text input, and `App.tsx` holds a single `response` that it *replaces* on every submit. There is no transcript, so "why not Salah?" is not a follow-up, it is a brand-new question the model answers with no idea what it just told you.

The five open decisions, resolved. Each of these is a recommendation with a reason, not a preference — argue with any of them at build time if the code says otherwise.

### 1. Conversation state lives server-side, one live conversation per team

Modelled on `team_suggestions` (ticket 15) and `gameweek_state` (ticket 13): a `conversations` table keyed by `team_id`, holding the message list as JSONB. v1 is explicitly single-user (`MY_TEAM_ID` is a constant), so "one live conversation per team" needs no session concept, no auth, and no new identity anywhere.

The alternative — the front end holds the transcript and posts it back each turn — is tempting because it needs no schema. Reject it. It loses the conversation on refresh, it makes the server unable to enforce anything about what it is being handed, and it means the client can silently rewrite history the model then treats as its own. The existing convention already solves this and costs one table.

**Retention:** a conversation is scoped to the planning gameweek, discarded when it rolls over, exactly like an Instinct (ticket 13). Advice about a deadline that has passed is not context, it is noise, and carrying it forward would quietly re-anchor next week's reasoning on last week's argument.

### 2. ADR-0004 holds via a `respond_to_user` terminal — and that is the dangerous part

The ticket's own suggested shape is right: add a `respond_to_user` tool alongside the two submit tools, so a clarifying reply or a counter-argument is a **tool call like any other** and `tool_choice: {"type": "any"}` is untouched. A pick still only ever comes from `submit_recommendation`'s player-id enum. No prose is ever parsed for a decision.

**But note what this creates, because it is not obvious and it undermines two shipped tickets.** Ticket 22's legality gate and ticket 25's plan validator both work by refusing a submit and handing the model an `is_error` tool result to revise against. That mechanism depends on the submit tools being the *only* way out. Add a third terminal and the model has an escape hatch: told "this transfer is illegal", it can now answer in prose instead of fixing the plan — and the user gets a fluent reply where they should have got a corrected recommendation.

So `respond_to_user` must be refused in exactly the situation it would be abused in: after a validator rejection, the next call has to be a submit. This needs its own test, and it is the single most important one in the ticket.

### 3. A challenge gets reconsideration, not capitulation

`fpl_rules.py` already instructs the honest assessment "even when it contradicts what the manager seems to want", and ticket 11's `PUSHBACK:` exists so the app can disagree. A conversational mode that folds under repetition is worse than no conversation at all — it turns the coach into a mirror, and `preferences.md` is explicit that the final call has to be justifiable as the *user's own reasoning*, which is impossible if the app just agrees.

Testable as: repeat an objection the evidence does not support three times, and the position must hold with its reasoning restated, not soften. Conversely, supply a *new fact* and it must genuinely move.

### 4. `known_players` growth is a measurement task, not a design decision

The enum grows as tools return results, and `claude.py` deliberately excludes the submit tools from the cached prefix *because* that enum changes ([[project_prompt_caching_reasoning]]). A longer conversation makes that worse, not better.

Do not pre-optimise. Measure a typical challenged exchange against a single-shot one first (already an AC). If it bites, the cheap fix is bounding the enum to players seen in the last N turns — but that interacts with citations (ADR-0014 looks a `citation_id` up from what was actually returned), so it is not free and should not be done speculatively.

### 5. Retry is per-turn, not per-conversation

`_MAX_ATTEMPTS = 2` currently restarts a stuck exchange from scratch, which is incoherent once there is a transcript — "from scratch" would discard the user's own messages. Retry the current turn only; the conversation is durable state and is never rebuilt.

### Scope note: the front end is half this ticket

The Ask tab becomes a transcript rather than a single card. That is real work and should not be discovered late: message list, the existing `RecommendationCard` / `TransferPlanCard` rendering *inside* a turn rather than as the whole view, and a follow-up input that stays available after an answer lands. `RememberNote` already proves a non-Recommendation response can render in that column.

### Sequencing

Build after ticket 24 is verified. 27 touches `claude.py`'s terminal contract, which is the same code path ticket 22's legality gate and ticket 25's plan validator depend on — doing that while 24's changes are still unverified would make a failure hard to attribute.

Ticket 25's roadmap is the best argument for this ticket and the best first customer: ADR-0016 keeps the plan on disk and the check-in reports which steps stopped holding, but the user currently cannot *discuss* any of it — only ask a fresh, context-free question about a plan the model has forgotten writing.

### Added acceptance criteria

- [x] After a validator rejection (ticket 22 or ticket 25), the model cannot answer with `respond_to_user` — the next call must be a submit
- [x] A conversation is scoped to the planning gameweek and does not survive it
- [x] Conversation state is server-side; a browser refresh does not lose the transcript
- [x] Retrying a stuck turn does not discard the user's earlier messages
- [x] The Ask tab renders a transcript, with follow-up available after an answer

---

**2026-08-30 — built.**

The Ask tab is a conversation. `ClaudeReasoning` gains a third terminal, the transcript is server-side and gameweek-scoped, and `App.tsx` renders turns instead of replacing a single answer.

New: `conversation.py`, the `conversations` table, `ConversationReply` / `ConversationTurn` / `Conversation` models, `GET`/`DELETE /conversation`, `Transcript.tsx`, and 18 tests.

### The escape hatch, which was the real work

`respond_to_user` is a terminal alongside the two submit tools, so `tool_choice: {"type": "any"}` is untouched and a clarifying reply is a tool call like any other. ADR-0004 holds: the tool has **one property, `text`** — no player id field exists, so the conversational exit is structurally incapable of naming a decision.

The danger is not that shape, it is what a third exit does to tickets 22 and 25. Both work by **refusing** a submit and handing the model an `is_error` tool result to revise against, and that mechanism only holds while the submit tools are the *only* way out of the loop. Add a prose exit and the model, told "this transfer is illegal", can explain itself instead of fixing it — and the user gets a fluent paragraph where they should have got a legal recommendation.

So a `must_submit` latch is set the moment a validator refuses, and while it is set `respond_to_user` is bounced through the same `is_error` channel:

> You cannot reply in prose here. A move you submitted was refused as illegal, and the user needs a corrected decision rather than an explanation.

The latch is never cleared within a turn: after being told a move is impossible there is no legitimate reason to stop and chat — if nothing legal is worth doing, `roll` says exactly that. The tool's own description also flips to `DISABLED right now` so a compliant model does not burn a turn discovering it. `test_prose_is_refused_immediately_after_a_validator_rejection` is the test that matters most in this ticket.

### Reconsideration, not capitulation

The tool description is explicit, because a coach that folds under repetition is a mirror and that is worse than one that cannot converse:

> Use this to reconsider, not to capitulate. If the evidence still supports what you said, hold that position and say why — agreeing because the user pushed twice is worse than disagreeing. If they have given you a genuinely new fact, weigh it and change your mind openly.

That is `fpl_rules.py`'s existing instruction (give the honest assessment "even when it contradicts what the manager seems to want") carried into a setting where the pressure to fold actually exists. Asserted in a test so it cannot be quietly dropped.

### What the transcript stores, and what it does not

**Turns, not the Anthropic message list.** Tool calls and tool results are *working*, not conversation. Persisting them would grow without bound, would mean writing provider SDK objects into our own schema (ADR-0003 keeps the provider behind the interface), and would re-send lookups every turn that the model can redo more cheaply. A finalised turn is replayed to the model as a summary sentence instead.

That has a property worth naming: **a rendered turn cannot smuggle a player id.** It is prose describing a past decision, so nothing in the history can become this turn's pick — the only route to a player is still a fresh submit against a live enum. ADR-0004 survives multi-turn without needing a second mechanism.

The cost is that the model cannot see its own earlier tool results and may re-fetch. Prompt caching absorbs most of it, and the alternative was worse.

**A finalised turn keeps its structure.** A Recommendation is stored and rendered as a Recommendation, not flattened to prose — ticket 17's citations are most valuable exactly where the user is arguing about whether the reasoning holds.

### The other resolved decisions

- **Server-side, one live conversation per team**, keyed by `team_id` like `team_suggestions` and `gameweek_state`. The front end holding the transcript needs no schema and was rejected: it loses everything on refresh and lets the client rewrite a history the model then treats as its own words.
- **Gameweek-scoped**, discarded on read when the gameweek rolls, exactly like an Instinct. Discarded lazily rather than by a cleanup job — ADR-0002 has no scheduler to run one.
- **Retry is per-turn.** `_MAX_ATTEMPTS` used to restart a stuck exchange with a fresh message list, which was fine when an exchange was one question; with a durable transcript that would discard the user's own messages. The turn is retried against the same history.
- **A reply is not a decision.** Nothing is written to `recommendation_log` (ADR-0012) or stored as a roadmap (ADR-0016) for one — there is no pick to score and no plan to follow.
- **A remembered Preference/Instinct never enters the transcript.** It is a standing note (ADR-0010) already carried in the system prompt; replaying it as conversation every turn would state it twice.

### Front end

`App.tsx` no longer holds one replaceable `response`. It loads `/conversation` on mount, shows the user's question optimistically (a reasoning call runs for many seconds, and a question that vanishes without appearing anywhere reads as a dropped request), then re-reads the server transcript rather than appending locally — the server is the authority on what was said. The input's placeholder changes to "Push back, or ask something else…" once an exchange is under way, and there is a "Start a new conversation" reset: a conversation that has wandered is cheaper to abandon than to argue back on course.

### Not done

- **Token cost of a challenged exchange versus a single-shot one is not measured.** It is an AC and it needs real model runs against real questions; left open rather than guessed at. The `known_players` enum growing across turns is the thing to watch ([[project_prompt_caching_reasoning]]) — but do not pre-optimise it, because bounding the enum interacts with ADR-0014's citation lookup and is not free.
- **A roadmap step cannot yet be questioned individually.** The AC says a step should be revisable without regenerating the whole plan. Conversation reaches the plan as a whole today; per-step revision needs the model to be able to name a step, which is a `submit_plan` schema question rather than a conversational one.

### Per-step roadmap revision — built 2026-09-03

The criterion was left open because the conversational half shipped without ever connecting to ticket 25's Roadmap. Two things were missing, and the first turned out to be the real one.

**The model could not see the plan.** There was no tool exposing the live Roadmap. Asked "why am I rolling in GW5?", the model had no way to learn a plan existed, so the only thing it could do was build a new one — the exact behaviour this criterion rules out, and not a matter of it choosing badly. A step cannot be revised without first being read.

**`get_roadmap`** (`tools/get_roadmap.py`) returns the live plan with every step's gameweek, decision and both player ids, plus an explicit `revisable` line telling the model it may change a single step. That last part is not decoration: a model that can see a plan but does not know it is allowed to touch one step of it will regenerate the whole thing out of caution.

**`revise_plan_step`** is a fourth terminal in `claude.py`, alongside `submit_recommendation`, `submit_plan` and `respond_to_user`. It carries one gameweek, one replacement step, and a `note` for the user.

- The step is built by `_build_revision` through the **same `_step_pick` and `_build_evidence`** as a `submit_plan` step. Sharing those is the point rather than a convenience — a revised step lands in the same stored plan as a submitted one, so it must be unable to name a player or a citation the model was never shown, by the same mechanism. ADR-0004 is untouched.
- It is named by **gameweek, not by index**. A step's identity to the user is the week it happens in, and an index into a list the model was shown is exactly the kind of positional reference that breaks when the list is re-sorted.
- It is inside the `must_submit` latch. Ticket 22 and 25's refusals only hold while every exit is a submit; a fourth exit that escaped the latch would let the model talk its way out of an illegal move via a "revision".

**`merge_step`** (`plan_rules.py`, pure) rebuilds the step list with one element swapped. Every other step is carried through **by identity** — the test asserts `revised.steps[0] is original.steps[0]` — and the original plan is never mutated, because a Roadmap is versioned and never overwritten (ADR-0016) and the version being revised has to survive intact to remain what the new version is compared against.

It **refuses rather than guesses** in the two cases where a plausible answer exists and is not safe:

| case | plausible wrong answer | what happens |
| --- | --- | --- |
| gameweek names no step | treat it as an insertion | refused, and the message lists the gameweeks that do exist |
| gameweek names two steps | revise the first | refused as ambiguous, told to use `submit_plan` |

`submit_plan` explicitly allows two steps in one gameweek (a free transfer plus a hit), so the second case is real rather than theoretical. Both refusals reach the model as an `is_error` tool result, so it gets a turn to name the step properly — the same channel ticket 22 uses.

**The merged plan is re-validated in full.** `_make_validator` merges the revision into the live roadmap and runs `plan_problems` over the *whole* result, not the one step. That is load-bearing: a step that is perfectly legal on its own can spend money a later step needed, and the cumulative `walk_plan` is what catches it. Doing the merge inside the validator rather than in the route is what keeps a refusal in front of the *model* instead of in front of the user.

**The note reaches the user.** The route merges before writing the transcript, so `assistant_turn` stores the note as the turn's `text` alongside the plan — a shape `ConversationTurn` already allowed. `render_history` puts the note ahead of the plan summary on the next turn, which matters more than it looks: without it the model sees only the new plan, has no record that it already conceded a point, and is liable to re-argue the step it just revised. The frontend renders it as a quiet accent-bordered line above the plan card (`.turn-revision`); a plan that was not a revision has no note and renders exactly as before.

**Storage is unchanged and deliberately so.** The merged plan falls through to the existing `save_roadmap`, so a revision becomes a **new version** like any other plan. Revising one step is precisely where the version history earns its keep — a single-step edit is the change most likely to be forgotten, and "last week's roadmap said something different" is only answerable if last week's roadmap still exists.

- `tests/test_merge_step.py`, 10 cases, pure — including that a refusal leaves the plan untouched.
- Frontend `tsc -b` and `oxlint` both clean.

**Still open on this ticket:** the token-cost comparison. That needs real paired runs (a single-shot answer versus a challenged exchange) and is a measurement, not a build.
