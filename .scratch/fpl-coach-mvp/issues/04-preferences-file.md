# 04 — Preferences file

**What to build:** Standing Preferences (ADR-0010) — a single flat text file loaded in full into every system prompt, distinct from a one-off, single-conversation ask. The user can edit the file directly, or tell the AI "remember: X" to have it appended. Horizon (this week / multi-week / season) stays a live-resolved parameter of the question, never persisted here.

**Blocked by:** 01 — Backend walking skeleton

**Status:** done

- [x] A Preferences file exists and is loaded in full into every Reasoning call's system prompt
- [x] Setting a Preference (e.g. "I dislike knee-jerk transfers") changes how subsequent Recommendations are framed
- [x] Telling the AI "remember: X" appends a new line to the Preferences file
- [x] A one-off instruction given within a single conversation (e.g. "give me the safest pick this week") affects only that conversation and is confirmed absent from the Preferences file afterward

## Comments

Preferences live in a flat `backend/preferences.md` (path overridable via `PREFERENCES_PATH`, gitignored — personal data, not versioned), per ADR-0010. `fpl_coach/preferences.py` holds `load_preferences()`, `append_preference()`, `build_system_prompt()`, and `extract_remember_instruction()`. `Reasoning.reason()` gained a `system: str = ""` parameter, threaded through to `ClaudeReasoning` (passed as the Anthropic `system` param, omitted entirely when empty) and `FakeReasoning` (stores it as `last_system` for test inspection).

`POST /recommendations` now: (1) checks the question against `remember: X` first — if matched, appends to the Preferences file and returns a `RememberAck` short-circuit *without* touching the DB or calling Reasoning at all (deterministic, no wasted Claude call, consistent with ADR-0004's preference for determinism where possible); (2) otherwise loads Preferences, builds the system prompt, and calls Reasoning as before. Response model is `Recommendation | RememberAck`, kept as a clean union rather than overloading the Recommendation schema with optional fields it doesn't need. Frontend (`App.tsx`) was extended to detect the `RememberAck` shape and render a "Remembered: …" confirmation instead of the recommendation card — the same single chat box now supports both, as ADR-0010's "tell the AI" implies.

Tests: unit tests for the preferences module (file-absent, append/reload, prompt building, remember-prefix parsing); Seam-1 API tests using a spy Reasoning double to assert the system prompt plumbing, that a `remember:` question short-circuits before Reasoning is ever called, and that a one-off question leaves the Preferences file untouched; a new Seam-2 integration test asserting a real-Claude Recommendation's risks are reframed by an injected Preference (all 3 integration tests — including the 2 pre-existing ones — currently fail identically on `ANTHROPIC_API_KEY` not being set locally, same caveat as issue 03; not a regression). Manually smoke-tested both the `remember:` short-circuit and the normal recommendation path end-to-end over real HTTP against a running dev server + real Supabase, then cleaned up the seeded verification data and temp files. Full non-integration suite: 16 passed, 3 deselected.

**Correction (2026-08-08):** AC2 was originally checked off on the strength of the Seam-2 test alone, but that test has never actually run — `ANTHROPIC_API_KEY` isn't set locally, so nothing has confirmed a real Preference actually changes a real Claude Recommendation's framing. Unchecked it and moved Status to `ready-for-human`: needs someone with the API key to run `uv run pytest -m integration` (or supply the key so it can be run here) before this can honestly be called done. Everything else on this ticket is independently verified without Claude (plumbing, persistence, isolation) and stays checked.

**Resolved (2026-08-17):** `ANTHROPIC_API_KEY` was added to `backend/.env`. Running `uv run pytest -m integration` surfaced two real bugs unrelated to this ticket's own scope, both in the shared `ClaudeReasoning` call path used by every Claude-backed test:

1. `temperature=0` is rejected by `claude-sonnet-5` (`400: temperature is deprecated for this model`) — removed the parameter from `reasoning/claude.py`. Determinism (ADR-0004) doesn't depend on it; it's enforced by the constrained `submit_recommendation` tool schema.
2. Without a forced `tool_choice`, Claude could end a turn with plain text instead of calling `submit_recommendation` — added `tool_choice={"type": "any"}` to force a tool call every turn.

With both fixed, `test_preference_changes_how_the_real_claude_recommendation_is_framed` passes: a real Claude Recommendation's risks are reframed by an injected Preference. AC2 checked, status moved to `done`.
