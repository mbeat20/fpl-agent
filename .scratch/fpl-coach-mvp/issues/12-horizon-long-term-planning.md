# 12 — Horizon-aware long-term planning

**What to build:** "What should my long-term transfer plan be?" resolves a longer Horizon (multi-gameweek or season-long, per CONTEXT.md) live from the question itself — never persisted — and produces a multi-week plan built on top of the transfer-recommendation logic from ticket 11, rather than a single-gameweek pick.

**Blocked by:** 11 — Transfer decisions + pushback

**Status:** done

- [x] The Horizon (this gameweek / multi-week / season) is inferred from the question asked, not stored anywhere
- [x] Asking "what should my long-term transfer plan be?" returns a plan spanning multiple gameweeks, not a single pick
- [x] The multi-week plan cites the same kind of evidence (stats, creator opinions, risk) as a single-gameweek Recommendation, appropriately extended across the horizon
- [x] A single-gameweek question asked in the same session still resolves to a single-gameweek Recommendation, confirming the two horizons don't interfere with each other

## Comments

**2026-08-20:** Built as a separate `TransferPlan` response type (not a field bolted onto `Recommendation`), so the existing single-gameweek response shape is untouched. Claude is offered two submit tools every turn (`submit_recommendation`, scoped to "THIS GAMEWEEK only" in its description, and `submit_plan`, `minItems: 2` as a hint only since Anthropic tool schemas aren't server-validated — the real guarantee is the integration test asserting `len(steps) >= 2`). Only the plan's nearest-gameweek step is written to `recommendation_log`: `record_pending_outcomes` (ADR-0012) only ever resolves the immediately-prior gameweek's rows, so logging every future-dated step would leave them permanently unresolved, polluting rather than building the v2 accuracy history.

Full non-integration suite (105 tests) green, plus all 16 `@pytest.mark.integration` tests (real Claude, real Voyage) green after one fix. `test_claude_produces_a_multi_week_plan_for_a_long_term_question` first failed against real Claude: asked for a multi-week plan with a transfer worked in "at some point," Claude returned steps at gameweeks `[8, 9, 9, 10, 11]` — a legitimate plan (two moves in one gameweek, e.g. a free transfer plus a hit), but the test had invented a stricter "each step its own gameweek" assertion that isn't actually one of this ticket's ACs. Loosened the assertion to what's actually required: steps ascending and genuinely spanning more than one gameweek. That surfaced a real bug while fixing it — `routes.py` picked the nearest step as `steps[0]`, but nothing in the `submit_plan` schema or its description requires Claude to emit steps in gameweek order, so an out-of-order response would have logged the wrong gameweek's row (and per the same ADR-0012 constraint above, silently left it unresolved forever). Fixed to `min(result.steps, key=lambda step: step.gameweek)`; `test_ask_logs_only_the_plans_nearest_step`'s `_PlanSpyReasoning` now deliberately emits steps out of order so this is covered without spending real API calls.

Also added `tests/test_claude_reasoning.py` (mocked-client regression tests for `ClaudeReasoning`'s malformed/incomplete tool-call retry handling) alongside this ticket's own work — not strictly issue-12 scope, but written while in this code.

Website-source ingestion (Fantasy Football Hub, LiveFPL, etc.) was raised in the same conversation as a follow-on and is tracked separately as ticket 14, `needs-triage` pending scope decisions — X accounts already work today via ticket 06's `fpl-add-creator` with zero new code.
