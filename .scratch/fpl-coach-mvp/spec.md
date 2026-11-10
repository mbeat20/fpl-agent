# AI FPL Coach — v1 (Personal Team Q&A)

Status: ready-for-agent

## Problem Statement

Every gameweek, making good Fantasy Premier League decisions (captaincy, transfers, chip timing) means manually gathering and reconciling official FPL data, underlying stats, injury/team news, and scattered opinions from trusted creators across X and YouTube — then weighing it all under time pressure, often emotionally, right before a deadline. Generic chatbot FPL advice doesn't know the user's actual team, doesn't explain its reasoning, and doesn't remember what the user does and doesn't value in a decision.

## Solution

A personal AI FPL Coach that, on demand, ingests the user's own team plus official data, statistics, and trusted creator opinions; stores it as structured facts and searchable text; and answers questions about the user's own team with evidence-backed Recommendations. Every Recommendation states its confidence, evidence, risks, and alternatives, so the user understands *why*, not just *what*. v1 is single-user, own-team-only: no social features, no other users, no monetisation.

## User Stories

1. As the sole user, I want to trigger a data refresh on demand before I ask questions, so that my Recommendations are based on current gameweek data.
2. As the sole user, I want the system to pull my current team from the official FPL API, so that Recommendations are grounded in my actual squad, not a generic one.
3. As the sole user, I want official FPL data (prices, ownership, form, fixtures, injury status) ingested as part of the refresh, so I don't have to gather it myself.
4. As the sole user, I want Understat's xG/xA stats ingested for players relevant to my team and transfer targets, so Recommendations can catch players over- or under-performing their underlying numbers.
5. As the sole user, I want trusted X creator posts ingested via Grok, so I get their opinions without paying per-read for X's own API.
6. As the sole user, I want each creator's raw opinion extracted into a structured Creator Stance (player, buy/sell/hold/captain, confidence, reason, creator), so the system can reason over opinions consistently instead of re-reading raw text every time.
7. As the sole user, I want to manually assign a trust tier (High/Medium/Low) when I add a creator, so my own judgment of their reliability shapes how much weight their Stances carry.
8. As the sole user, I want to ask "who should I captain?" and get a single clear pick with confidence, evidence, risks, and an alternative, so I can decide quickly without re-deriving the analysis myself.
9. As the sole user, I want the captain/transfer pick itself to come back identical every time I ask against the same underlying data, so I can trust it's a real judgment and not sampling noise.
10. As the sole user, I want the AI to vary how it frames alternatives and explains risk between asks, so the conversation doesn't feel robotic even though the core pick doesn't change.
11. As the sole user, I want to ask "should I make a transfer?" and get a recommendation that weighs the cost of a hit against the expected gain, so I don't make emotional transfers.
12. As the sole user, I want to ask "should I roll my transfer?", so I can decide whether banking a transfer is worth it this week.
13. As the sole user, I want to ask "is this player worth buying?" about any player, not just ones already in my team, so I can evaluate transfer targets before committing.
14. As the sole user, I want to ask "what do trusted creators think about [player]?" and get a synthesized view across multiple creators weighted by trust tier, so I don't have to individually read every creator's take.
15. As the sole user, I want to search creator opinions semantically (e.g. "find opinions about Newcastle's fixture run"), so I can explore looser questions than an exact player-name lookup.
16. As the sole user, I want to ask "which players are expected to rise in price?", so I can act before a price change costs me value.
17. As the sole user, I want to ask "what are the risks this week?" and get a summary of injury, rotation, and fixture risk across my whole squad, so nothing catches me by surprise.
18. As the sole user, I want to ask "what should my long-term transfer plan be?" and get a multi-gameweek plan — a longer Horizon than a single pick — so I can plan ahead rather than only reacting week to week.
19. As the sole user, I want to say "talk me out of my current transfer" and have the AI genuinely push back with evidence when the data doesn't support it, rather than agreeing with me by default, so it protects me from my own knee-jerk decisions.
20. As the sole user, I want to record standing Preferences ("I dislike knee-jerk transfers", "I take captain risks when chasing rank") in a plain text file, so I don't have to restate my general approach every gameweek.
21. As the sole user, I want to edit my Preferences file directly, or tell the AI "remember: X" to append to it, so updating my standing preferences is low-friction.
22. As the sole user, I want a one-off instruction I give within a single conversation ("give me the safest pick this week") to apply only to that conversation, so it never silently overrides my standing Preferences.
23. As the sole user, I want every Recommendation to explicitly state its confidence, evidence, risks, and alternatives, so I understand why the AI is suggesting what it's suggesting, not just what it's suggesting.
24. As the sole user, I want the outcome of each Recommendation and Creator Stance (e.g. did the price move as predicted, did the captain score well) recorded automatically once it's known, so a future version can score accuracy without needing to backfill history first.
25. As the developer, I want the reasoning model accessed through one interface, so I can compare different models' recommendation quality later without rewriting the tools or API layer.
26. As the developer, I want the backend (API, reasoning, tools) and the ingestion pipeline living in one Python project sharing the same data models, so I'm not maintaining duplicate schemas across two runtimes.
27. As the developer, I want the frontend to be a separate Vite + React project calling the backend over HTTP, so the API boundary is explicit and a future client (e.g. mobile) could reuse it.
28. As the developer, I want the structured data and the vector-searchable data both backed by one Supabase Postgres+pgvector instance, so I'm not running two database platforms for one dataset.
29. As the developer, I want Supabase used only for its Postgres+pgvector piece, so I'm not tempted to reach for Auth/Storage/Realtime before there's any real need for them.
30. As the developer, I want a Creator Stance's structured fields and its source raw text linked by reference rather than duplicated, so the structured and vector stores can't drift out of sync.
31. As the sole user, I want to log an early-week transfer or captaincy instinct (e.g. "thinking Rogers → Enzo") and have the AI surface it back to me when I ask about that same decision later in the week, so I can tell whether my thinking genuinely evolved or I'm just chasing a bandwagon.

## Implementation Decisions

**Language / runtime split** ([[ADR-0008]], [[ADR-0009]]): One Python project (FastAPI) holds the API, Reasoning layer, Tools layer, and the ingestion pipeline, sharing Pydantic models and a single Postgres access layer. The frontend is a separate Vite + React (TypeScript) SPA calling the backend's HTTP API. No shared code between the two beyond the API contract.

**Ingestion** ([[ADR-0002]]): Triggered on demand (an endpoint or CLI command in the backend project), not scheduled. Sources for v1: the official FPL API (team, prices, ownership, form, fixtures, injury status flags), Understat (xG/xA), and Grok's Live Search API for trusted X creator posts (chosen over a direct X API subscription — X's API has no free tier as of 2026). FBref, Sofascore, and YouTube creator content are out of scope for v1 (see Out of Scope). Injury/team news relies on the FPL API's own status flags for v1; no dedicated injury-news scraper is built.

**Extraction (Layer 2)**: An LLM step converts each piece of raw creator content into one or more structured Creator Stance records (player, buy/sell/hold/captain, confidence, reason, creator, trust tier inherited from the creator). One piece of raw content (a thread, a post) can yield multiple Stances.

**Storage** ([[ADR-0005]], [[ADR-0011]]): Supabase, used only for its Postgres+pgvector piece — no Auth, Storage, or Realtime. Structured tables: Players, Fixtures, Prices, Injuries, Creator Stances, Team history, Transfers. A Creator Stance's structured fields live in the structured tables; its source raw text is chunked and embedded into pgvector, linked back by reference — one record, two storage facets, not two separate concepts. Embeddings via Voyage AI (Claude, the reasoning model, has no embeddings API).

**Creator trust** ([[ADR-0001]]): Manually assigned trust tier (High/Medium/Low) per creator at add-time. No computed accuracy scoring in v1.

**Outcome logging** ([[ADR-0012]]): Each Creator Stance and Recommendation gets its real-world outcome recorded once known (next gameweek's price movement, points scored, whether a captain pick paid off), sourced from data already pulled during the weekly ingestion. Nothing reads this data in v1 — it exists so v2's accuracy scoring has history to compute from instead of starting cold.

**Tools (Layer 4)**: Plain function calls for v1 — `get_my_team`, `get_player`, `get_fixture`, `search_creator_opinions` (vector search), `get_injury_news`, `recommend_transfers`, `compare_players`, `predict_price_changes`. Not exposed via MCP yet; wrapping them in an MCP server is a deliberate later exercise once the tool set has stabilized.

**Reasoning (Layer 5)** ([[ADR-0003]], [[ADR-0004]]): Accessed through one interface — `reason(prompt, tools) -> Recommendation` — with Claude as v1's implementation. The primary pick within a Recommendation (who to captain, whether to transfer) is deterministic: identical inputs always produce the identical pick. Creativity (temperature, phrasing, which alternative gets surfaced) is confined to the framing around that pick, never the pick itself. Grok is scoped only to X ingestion and is not behind this interface.

**Horizon and Preferences** ([[ADR-0010]]): Horizon (this gameweek / multi-week plan / season-long) is a parameter of the question asked, resolved live from fixtures/data each time — never persisted. Preferences (standing traits, e.g. "dislikes knee-jerk transfers") live in a single flat text file, loaded in full into every system prompt; edited directly by the user or appended to on "remember: X". A one-off, single-conversation ask is never written to this file.

**Instincts**: A gameweek-scoped table, separate from the Preferences file, holding the user's own early-week notes on a transfer/captaincy call they're weighing. Logged via "remember: I'm thinking X" style input (same verb as Preferences, different destination — scoped by an active gameweek vs. permanent) or a dedicated `log_instinct` tool call. When a Recommendation touches the same player(s)/decision later that gameweek, the matching Instinct is pulled in and shown alongside the current Recommendation, prompting the user to reconcile any change. Cleared automatically once that gameweek's deadline passes — never accumulates across gameweeks like a Preference does.

**Domain vocabulary**: See `CONTEXT.md` for full definitions of Creator Stance, Recommendation, Horizon, and Preference — used consistently throughout this spec and any resulting tickets.

## Testing Decisions

Two seams, agreed with the user before writing this spec:

**Seam 1 — the backend's HTTP API** (FastAPI test client against a real test Postgres+pgvector instance). Seed known Players, Fixtures, Creator Stances (with trust tiers and outcome history), and Preferences; call the API with a question; assert the returned Recommendation's shape (confidence, evidence, risks, alternatives present) and — critically — that the core pick is identical across repeated calls against identical seeded state (ADR-0004). This is the primary, highest-value seam: it exercises the Tools layer, the data model, and the Reasoning interface's wiring together, closest to actual user-observable behavior.

**Seam 2 — the Reasoning interface** (`reason(prompt, tools) -> Recommendation`). Faked with a deterministic test double for all Seam-1 tests, since the real (Claude) implementation is slow, has a real cost, and isn't guaranteed byte-identical run to run. A small number of separate tests, explicitly marked (e.g. `integration`) and not part of the default suite, call the real implementation to confirm it satisfies the interface contract.

**Ingestion tests**: Fake the FPL API / Understat / Grok clients at their HTTP boundary with canned fixture responses (recorded real responses, not hand-written approximations, where feasible); assert ingestion writes correctly-shaped rows — including trust tier applied to new Creator Stances and injury status parsed from the FPL API's flags.

**What makes a good test here**: assert on external behavior — API response shape and content, database rows, the Recommendation's fields — never on implementation details like the exact prompt text sent to Claude or the internal order tools are invoked in, beyond what's externally observable (e.g. which tables got written).

**Prior art**: none — this is a greenfield codebase; these tests establish the pattern for what follows.

## Out of Scope

- Multiple specialised agents (transfer planner, chip planner, etc.) — one reasoning model, many tools, for now.
- MCP servers for the Tools layer — plain function calls in v1, MCP as a deliberate later exercise.
- Computed creator accuracy scoring and the resulting weighted consensus — deferred to v2, though outcome logging starts in v1 so v2 isn't starting cold.
- Scheduled/automated ingestion — on-demand only.
- FBref and Sofascore as stats sources — Understat + the official FPL API only.
- YouTube creator content — X (via Grok) only.
- A dedicated injury-news/press-conference source — the FPL API's own status flags only.
- Social features, other users, monetisation.
- Supabase Auth, Storage, and Realtime.
- Everything in the original plan's "Future Features" list (wildcard optimisation, chip planning, price prediction, fixture simulation, live commentary, debate mode, season planning, bench optimisation, differential finder, mini-league rival analysis, automated reports, push notifications).
- Whether this becomes a commercial product later.

## Further Notes

- Full decision history lives in `docs/adr/` (ADR-0001 through ADR-0012; ADR-0006 and ADR-0007 are superseded by ADR-0008 and ADR-0009 respectively) and `CONTEXT.md` for domain vocabulary.
- The Preferences file lives at `preferences.md` in the repo root, seeded with season-long standing rules distilled from `docs/New FPL Draft + Q&A!.txt`. Issue 04 wires it into every Reasoning call's system prompt.
- The reasoning model default is Claude but is swappable by design (ADR-0003); comparing it against other models is an explicit future exercise, not a v1 goal in itself.
