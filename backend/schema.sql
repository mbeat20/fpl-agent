-- Issue 01: minimal schema to hold a Team and its Players.
-- Apply with: psql "$DATABASE_URL" -f schema.sql

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    position TEXT NOT NULL CHECK (position IN ('GK', 'DEF', 'MID', 'FWD')),
    club TEXT NOT NULL,
    price NUMERIC(4, 1) NOT NULL
);

-- Issue 03: populated by real FPL API ingestion; NULL for manually seeded rows.
-- CREATE TABLE IF NOT EXISTS above is a no-op once the table exists, so these
-- columns need their own ALTER TABLE to reach an already-created players table.
ALTER TABLE players ADD COLUMN IF NOT EXISTS ownership NUMERIC(5, 2);
ALTER TABLE players ADD COLUMN IF NOT EXISTS form NUMERIC(4, 1);
ALTER TABLE players ADD COLUMN IF NOT EXISTS status TEXT CHECK (status IN ('a', 'd', 'i', 's', 'u', 'n'));
ALTER TABLE players ADD COLUMN IF NOT EXISTS news TEXT;

-- Issue 03: fixture data ingested from the official FPL API.
CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY,
    gameweek INTEGER,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_difficulty SMALLINT,
    away_difficulty SMALLINT,
    kickoff_time TIMESTAMPTZ
);

-- Issue 05: `players` is the user's 15-player squad; this is the FPL id each
-- squad row corresponds to, so squad and pool players share one id namespace
-- (get_my_team returns it as Player.id). NULL for manually seeded rows.
ALTER TABLE players ADD COLUMN IF NOT EXISTS fpl_element_id INTEGER;

-- Issue 05: the full FPL player pool (every element from bootstrap-static,
-- not just the user's squad), refreshed on every ingestion cycle, so
-- get_player/compare_players can look up any player, not only squad members.
-- xg/xa are matched in from Understat by name best-effort and stay NULL when
-- no confident match was found.
CREATE TABLE IF NOT EXISTS fpl_players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    position TEXT NOT NULL CHECK (position IN ('GK', 'DEF', 'MID', 'FWD')),
    club TEXT NOT NULL,
    price NUMERIC(4, 1) NOT NULL,
    ownership NUMERIC(5, 2),
    form NUMERIC(4, 1),
    status TEXT CHECK (status IN ('a', 'd', 'i', 's', 'u', 'n')),
    news TEXT,
    xg NUMERIC(6, 2),
    xa NUMERIC(6, 2)
);

-- Issue 08: net-transfer and price-change signals from the same
-- bootstrap-static elements, for predict_price_changes. NULL until the next
-- refresh for rows inserted before this column existed.
ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS transfers_in_event INTEGER;
ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS transfers_out_event INTEGER;
ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS cost_change_event SMALLINT;

-- Issue 06: trusted X creators, manually assigned a trust tier at add-time
-- (ADR-0001).
CREATE TABLE IF NOT EXISTS creators (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    x_handle TEXT NOT NULL UNIQUE,
    trust_tier TEXT NOT NULL CHECK (trust_tier IN ('high', 'medium', 'low'))
);

-- Issue 06: raw ingested content per creator, pre-extraction (Layer 1).
-- xAI's X Search tool returns Grok's own synthesized paraphrase of each
-- cited post plus that post's URL, not the post's verbatim text (confirmed
-- against a real API call, 2026-08-18 -- docs.x.ai/developers/tools/x-search)
-- -- content_text is that paraphrase, not a raw tweet body. UNIQUE
-- (creator_id, source_url) + ON CONFLICT DO NOTHING makes re-ingestion
-- idempotent, since content accumulates across runs rather than getting
-- replaced like the ticket-03 team/player refresh does.
CREATE TABLE IF NOT EXISTS creator_content (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    content_text TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (creator_id, source_url)
);

-- Issue 06: structured Creator Stances extracted (Layer 2, an LLM step) from
-- creator_content -- one piece of content may yield multiple Stances. The
-- vector half of ADR-0005 (embedding creator_content's source text) lives
-- separately in content_chunks below.
-- fpl_player_id deliberately has no FK: ingestion/pipeline.py does
-- `DELETE FROM fpl_players` wholesale every refresh, which would block or
-- cascade-drop this table -- follows players.fpl_element_id's existing
-- plain-INTEGER, best-effort-match, NULL-when-unmatched convention instead.
CREATE TABLE IF NOT EXISTS creator_stances (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    source_content_id INTEGER NOT NULL REFERENCES creator_content(id) ON DELETE CASCADE,
    player_name TEXT NOT NULL,
    fpl_player_id INTEGER,
    stance TEXT NOT NULL CHECK (stance IN ('buy', 'sell', 'hold', 'captain')),
    confidence NUMERIC(3, 2) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Issue 06 (criterion 4) / ADR-0005: the vector half of a Creator Stance --
-- creator_content.content_text chunked and embedded via Voyage AI
-- (voyage-3, 1024 dims, confirmed live 2026-08-18) so ticket 09's semantic
-- search can find opinions pgvector similarity, then resolve back through
-- source_content_id -> creator_content -> creator_stances for the
-- structured fields (ADR-0005: many-Stances-to-one-source, not 1:1).
-- ON DELETE CASCADE from creator_content completes the
-- creators -> creator_content -> content_chunks chain, so existing
-- test_creator_pipeline.py teardown (DELETE FROM creators ...) cleans this
-- up too with no new fixture code.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS content_chunks (
    id SERIAL PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES creator_content(id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Issue 13: one row per team, refreshed by run_ingestion (which already
-- resolves is_current/is_next from bootstrap-static for the FPL API calls it
-- makes) -- keyed by team_id (not a global singleton) so a fake ingestion
-- run in tests (TEST_TEAM_ID) can never clobber the real team's (MY_TEAM_ID)
-- gameweek, and so ON DELETE CASCADE cleans it up whenever a test's team row
-- is deleted, same convention as creators -> creator_content.
CREATE TABLE IF NOT EXISTS gameweek_state (
    team_id INTEGER PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Issue 13: the user's own early-week transfer/captaincy notes (CONTEXT.md's
-- Instinct), distinct from preferences.md's standing Preferences despite the
-- shared "remember: X" phrasing. Gameweek-scoped, not carried forward --
-- reads filter by gameweek_state's current row, and run_ingestion purges
-- rows left over from a prior gameweek. fpl_player_id-less by design: an
-- Instinct is the user's own free-text note (e.g. "thinking Rogers to
-- Enzo"), not a structured pick.
CREATE TABLE IF NOT EXISTS instincts (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Issue 10 / ADR-0012: enough identity on each Recommendation to later match
-- it against what actually happened (player, gameweek), plus outcome
-- columns a subsequent run_ingestion cycle fills in once known (next
-- gameweek's price movement, points scored -- for a captain pick, "paid
-- off" is just outcome_points read in context, no separate column). Nothing
-- in v1 reads outcome_* -- this only exists so v2's accuracy scoring
-- (ADR-0001) has history instead of starting cold. gameweek and player_id
-- are nullable: a Recommendation can in principle be made before any team
-- ingestion has ever resolved a gameweek, and player_id has no FK for the
-- same wholesale-fpl_players-refresh reason as creator_stances.fpl_player_id
-- above.
CREATE TABLE IF NOT EXISTS recommendation_log (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER,
    player_id INTEGER,
    player_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome_price_change NUMERIC(4, 1),
    outcome_points INTEGER,
    outcome_recorded_at TIMESTAMPTZ
);

-- Issue 10: same outcome shape as recommendation_log above, added to the
-- existing creator_stances table. gameweek is nullable -- creator ingestion
-- (creator_pipeline.py) can run before team ingestion has ever established
-- a current gameweek for MY_TEAM_ID.
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS gameweek INTEGER;
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS outcome_price_change NUMERIC(4, 1);
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS outcome_points INTEGER;
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS outcome_recorded_at TIMESTAMPTZ;

-- Issue 15: the Team dashboard's unprompted weekly suggestion, generated
-- on-demand (ADR-0002: no scheduler) the first time GET /team/suggestion is
-- called for a given gameweek, then cached here so every later call that
-- gameweek is a read, not a fresh Claude call. One row per team (same
-- convention as gameweek_state) -- regenerating for a new gameweek
-- overwrites the previous row rather than accumulating a history this
-- endpoint has no use for (recommendation_log already keeps that trail).
-- Holds whatever reasoning.reason() returned, a single-gameweek
-- Recommendation or a longer-Horizon TransferPlan, as-is.
CREATE TABLE IF NOT EXISTS team_suggestions (
    team_id INTEGER PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER NOT NULL,
    recommendation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Issue 16: a Creator is followed on more than one platform, so the platform
-- identity moves out of creators.x_handle into its own table. x_handle stays
-- (ticket 06's fpl-add-creator keeps working, and the Grok/X leg still reads
-- it) but becomes nullable, because a YouTube-only creator has no X handle
-- and fabricating one would put a wrong identity in the trust record.
-- Postgres allows many NULLs under a UNIQUE index, so the existing
-- UNIQUE (x_handle) still holds for the creators that do have one.
ALTER TABLE creators ALTER COLUMN x_handle DROP NOT NULL;

-- external_id is the platform's own stable id, NOT the handle: for YouTube
-- that is the UC... channel id, resolved from the @handle once at add-time.
-- Handles get renamed; channel ids do not. UNIQUE (platform, external_id)
-- stops the same channel being attached to two creators, which would double
-- -count that creator's stances in ticket 19's consensus.
CREATE TABLE IF NOT EXISTS creator_sources (
    id SERIAL PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('x', 'youtube')),
    external_id TEXT NOT NULL,
    handle TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, external_id)
);

-- Issue 16: which platform a piece of content came from, and when the
-- creator actually published it. published_at is the important one -- until
-- now there was only fetched_at, so "what does this creator think NOW"
-- could not be distinguished from what they thought a month ago (ticket 18).
-- Nullable: Grok's X Search returns a paraphrase with no reliable post date,
-- so backfilling a guess would be worse than admitting the gap.
ALTER TABLE creator_content ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'x';
ALTER TABLE creator_content DROP CONSTRAINT IF EXISTS creator_content_platform_check;
ALTER TABLE creator_content ADD CONSTRAINT creator_content_platform_check
    CHECK (platform IN ('x', 'youtube'));
ALTER TABLE creator_content ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE creator_content ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE creator_content ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;

-- Issue 16: the second into the video this chunk starts at, so a pgvector
-- hit resolves back to a citable moment (youtu.be/<id>?t=<start_seconds>)
-- rather than just to a video. NULL for an X post, which has no timeline.
ALTER TABLE content_chunks ADD COLUMN IF NOT EXISTS start_seconds INTEGER;

-- Issue 16: a transcript's most valuable claims are often not about a single
-- player -- "wildcard around GW8", "Arsenal's run from GW6 is the best in
-- the game", "he'll be rotated in the cups". The ticket-06 shape (player_name
-- NOT NULL, stance IN (buy|sell|hold|captain)) could not store any of them,
-- so extraction silently threw them away.
--   * player_name becomes nullable, and `subject` carries the non-player
--     topic instead (a club, a chip, a fixture run) so the row is still
--     queryable rather than being prose in `reason` alone.
--   * the vocabulary widens. avoid/differential/bench are still
--     player-scoped; chip_timing/fixture_view are the ones that legitimately
--     name no player.
-- A stance never reaches submit_recommendation as a pick: ADR-0004's
-- pick_player_id enum is built only from tool results that return a real
-- Player (reasoning/claude.py's _record_known_players), and no creator
-- -opinion result carries one. Widening this cannot smuggle a non-player
-- into a pick.
ALTER TABLE creator_stances ALTER COLUMN player_name DROP NOT NULL;
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS subject TEXT;
ALTER TABLE creator_stances DROP CONSTRAINT IF EXISTS creator_stances_stance_check;
ALTER TABLE creator_stances ADD CONSTRAINT creator_stances_stance_check
    CHECK (stance IN ('buy', 'sell', 'hold', 'captain',
                      'avoid', 'differential', 'bench', 'chip_timing', 'fixture_view'));
ALTER TABLE creator_stances DROP CONSTRAINT IF EXISTS creator_stances_subject_check;
ALTER TABLE creator_stances ADD CONSTRAINT creator_stances_subject_check
    CHECK (player_name IS NOT NULL OR subject IS NOT NULL);

-- Issue 16: where in the source video this stance was expressed, so ticket
-- 17 can cite it as a deep link without re-deriving it from the chunks.
ALTER TABLE creator_stances ADD COLUMN IF NOT EXISTS start_seconds INTEGER;

-- ---------------------------------------------------------------------------
-- Issue 20: the user's actual FPL state.
--
-- fpl_rules.py -- the system prompt on every call -- already instructs the
-- model to check affordability "from the selling price of the player out plus
-- the money in the bank", and to weigh "minutes security". Neither existed in
-- the schema, so the model was being asked to satisfy a rule it had no data
-- for. run_ingestion was already fetching most of this and discarding it.
--
-- These live on `teams` rather than a parallel state table: one row per team,
-- and the teams row is UPSERTed (issue 10's cascade lesson), so nothing here
-- is wiped by the next ingestion cycle the way a delete+reinsert would.
ALTER TABLE teams ADD COLUMN IF NOT EXISTS bank NUMERIC(5, 1);
-- FPL's own `value`: the squad's total selling value PLUS the bank. Named
-- to match, because calling it squad_value invites an off-by-the-bank
-- error -- at GW1 every team reads value=100.0 with a 99.5 squad and 0.5
-- banked. The squad-only figure is team_value - bank, derived in
-- squad_state.sellable_value rather than stored twice.
ALTER TABLE teams ADD COLUMN IF NOT EXISTS team_value NUMERIC(5, 1);
-- Superseded within the same ticket: the first cut of this column was
-- named squad_value and held FPL's `value` verbatim, which reads as the
-- squad alone and is not.
ALTER TABLE teams DROP COLUMN IF EXISTS squad_value;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS event_transfers INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS event_transfers_cost INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS overall_rank INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS gameweek_points INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS total_points INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS points_on_bench INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS active_chip TEXT;
-- Which gameweek the picks/entry state above were read from. Distinct from
-- gameweek_state.gameweek (the planning gameweek): FPL only exposes picks for
-- a gameweek whose deadline has passed, so squad state legitimately lags the
-- gameweek being planned for.
ALTER TABLE teams ADD COLUMN IF NOT EXISTS squad_gameweek INTEGER;

-- Issue 20: the XI/bench split, its order, and the armband. position 1-11 is
-- the XI, 12-15 the bench in the order they would come on. players is still
-- wholesale replaced every run, so these are re-derived rather than migrated.
ALTER TABLE players ADD COLUMN IF NOT EXISTS squad_position SMALLINT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_captain BOOLEAN DEFAULT FALSE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_vice_captain BOOLEAN DEFAULT FALSE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS multiplier SMALLINT;
-- Season-total minutes, free from bootstrap.elements -- the cheap tier of
-- "minutes security". Per-fixture history lives in player_gameweek_history.
ALTER TABLE players ADD COLUMN IF NOT EXISTS minutes INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS starts INTEGER;

ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS minutes INTEGER;
ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS starts INTEGER;
ALTER TABLE fpl_players ADD COLUMN IF NOT EXISTS starts_per_90 NUMERIC(4, 2);

-- Issue 20: chips the user has actually played, from /entry/{id}/history/.
-- What remains is this set subtracted from bootstrap's chip catalogue, which
-- carries each chip's own start_event/stop_event window -- so "which chips do
-- I have left" needs no extra API call and no hardcoded chip list.
CREATE TABLE IF NOT EXISTS chips_used (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    gameweek INTEGER NOT NULL,
    PRIMARY KEY (team_id, name, gameweek)
);

-- Issue 20: per-fixture minutes, the expensive tier. element-summary is one
-- call PER PLAYER, so this is deliberately never fanned out across the ~700
-- pool players -- only the squad, plus any player the user asks about, and
-- only when the cached rows are behind the latest finished gameweek.
--
-- fpl_player_id has no FK on purpose: ingestion/pipeline.py does
-- DELETE FROM fpl_players wholesale every refresh, which would cascade-drop
-- this cache every cycle and re-incur every element-summary call. Same
-- plain-INTEGER convention as creator_stances.fpl_player_id.
CREATE TABLE IF NOT EXISTS player_gameweek_history (
    fpl_player_id INTEGER NOT NULL,
    gameweek INTEGER NOT NULL,
    minutes INTEGER NOT NULL,
    starts SMALLINT,
    total_points INTEGER,
    was_home BOOLEAN,
    opponent TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fpl_player_id, gameweek)
);

-- Issue 20: the entry's own per-gameweek record, from /entry/{id}/history/'s
-- `current` array -- the same response the chips list comes from, so this is
-- free. Rank matters beyond bookkeeping: chasing or protecting a rank is
-- exactly the standing context that should change risk appetite, and
-- preferences.md already refers to it.
CREATE TABLE IF NOT EXISTS entry_gameweek_history (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER NOT NULL,
    points INTEGER,
    total_points INTEGER,
    overall_rank INTEGER,
    gameweek_rank INTEGER,
    bank NUMERIC(5, 1),
    team_value NUMERIC(5, 1),
    event_transfers INTEGER,
    event_transfers_cost INTEGER,
    points_on_bench INTEGER,
    PRIMARY KEY (team_id, gameweek)
);

-- ---------------------------------------------------------------------------
-- Issue 19: the gameweek calendar -- specifically each gameweek's DEADLINE.
--
-- Until now the only time the database knew about a gameweek was
-- fixtures.kickoff_time, which is when the football starts, not when the
-- manager loses the ability to act. Ticket 19 has to place a piece of creator
-- content in the gameweek it was published FOR, and the boundary that decides
-- that is the deadline: a post published on Friday afternoon is previewing the
-- gameweek whose deadline it precedes, whatever gameweek the ingestion run
-- that fetched it happened to be tagged with.
--
-- Free to populate: bootstrap-static's `events` array is already fetched every
-- run_ingestion cycle and every field here was being discarded.
--
-- Global rather than teams-keyed -- the calendar is the same for everyone --
-- so nothing here is at risk from the teams cascade (issue 10). Upserted, not
-- delete+reinsert, for the same reason.
CREATE TABLE IF NOT EXISTS gameweeks (
    id INTEGER PRIMARY KEY,
    name TEXT,
    deadline_time TIMESTAMPTZ NOT NULL,
    finished BOOLEAN NOT NULL DEFAULT FALSE,
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    is_next BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------------
-- Issue 21: the club identity map, FPL team id -> names.
--
-- Every other table stores a club as its SHORT name ('ARS'), because that is
-- what run_ingestion resolves from bootstrap while it has the id map in hand:
-- fixtures.home_team/away_team, players.club, fpl_players.club. That works
-- until something outside ingestion meets a bare FPL team id and has nowhere
-- to look it up -- which is exactly why issue 20's on-demand minutes path
-- leaves `opponent` NULL: element-summary returns an opponent team id and no
-- id -> name map existed.
--
-- Short name stays the join key everywhere else; this table exists to resolve
-- an id, and to let a human or a model say "Arsenal" where the database says
-- "ARS". Free -- bootstrap['teams'] is already fetched every cycle.
CREATE TABLE IF NOT EXISTS clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS clubs_short_name_idx ON clubs (short_name);

-- ---------------------------------------------------------------------------
-- Issue 25: a Roadmap -- a multi-week plan the user is FOLLOWING, as opposed
-- to a TransferPlan, which is one answer to one question.
--
-- CONTEXT.md's Horizon is "resolved live from fixtures/data each time. Never
-- persisted", and that stays true: a Horizon is a property of a question. A
-- Roadmap is a different thing, and the difference is that the user can be
-- shown to have drifted from it. That requires it to still exist next week.
--
-- VERSIONED, NEVER OVERWRITTEN. This is the whole point, and it is the one
-- place this table deliberately departs from team_suggestions (issue 15),
-- which overwrites because a cache has no use for its own history. Here the
-- history IS the value: preferences.md asks to be able to say why a pick
-- changed -- genuine reassessment versus bandwagon influence -- and
-- overwriting destroys exactly that signal.
--
-- At most one live version per team, enforced by a partial unique index
-- rather than by convention. A second live roadmap is not a state the app
-- should be able to reach by accident.
CREATE TABLE IF NOT EXISTS roadmaps (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    -- The TransferPlan as-is, same JSONB convention as team_suggestions.
    plan JSONB NOT NULL,
    -- What the fixture calendar said about the clubs and gameweeks this plan
    -- touches, AT THE MOMENT IT WAS STORED. Without this the check-in can
    -- report what is true today but not what CHANGED -- and `fixtures` is
    -- wholesale replaced every run_ingestion, so a stored plan rots silently.
    fixture_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The planning gameweek when this version was created, so "how stale is
    -- this" is answerable without re-deriving it from the steps.
    from_gameweek INTEGER,
    -- The question that produced it, for the version trail. A roadmap the
    -- user cannot place is a roadmap they will not trust.
    prompt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL means live. Set when a newer version replaces this one.
    superseded_at TIMESTAMPTZ,
    -- Why it was replaced, and what differed. Written onto the OLD row at
    -- supersede time, so reading the history explains itself.
    supersede_reason TEXT,
    UNIQUE (team_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS roadmaps_one_live_per_team
    ON roadmaps (team_id) WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS roadmaps_team_version_idx ON roadmaps (team_id, version DESC);

-- ---------------------------------------------------------------------------
-- Issue 24: the player's PRICE in each gameweek, so a recommendation can be
-- scored on the price movement that actually followed it.
--
-- Outcome resolution used to read bootstrap-static's `cost_change_event`,
-- which is only ever *this* event's number -- so a row more than one gameweek
-- old could not be resolved without writing the wrong gameweek's data onto
-- it, and the code correctly refused to try. element-summary's history
-- carries `value` per gameweek (in tenths), which makes a late resolution
-- correct rather than approximate, and therefore makes catching up after a
-- skipped ingestion cycle possible at all.
--
-- Stored in millions to match players.price rather than in FPL's tenths.
ALTER TABLE player_gameweek_history ADD COLUMN IF NOT EXISTS value NUMERIC(4, 1);

-- ---------------------------------------------------------------------------
-- Issue 27: the live conversation, so a Recommendation can be argued with.
--
-- One row per team, same convention as team_suggestions (issue 15) and
-- gameweek_state (issue 13) -- v1 is single-user (MY_TEAM_ID is a constant),
-- so "one live conversation per team" needs no session concept, no auth and no
-- new identity anywhere in the app.
--
-- Server-side rather than held by the front end and posted back each turn.
-- That alternative needs no schema and was rejected: it loses the exchange on
-- refresh, and it lets the client rewrite a history the model then treats as
-- its own words.
--
-- GAMEWEEK-SCOPED. `gameweek` is stored so a conversation from a passed
-- deadline is discarded rather than continued -- the same rule instincts
-- follow. Carrying an argument across a deadline would re-anchor next week's
-- reasoning on a case about fixtures that have already been played.
--
-- `turns` holds the user-visible transcript, NOT the Anthropic message list.
-- Tool calls and tool results are deliberately not persisted: they are how an
-- answer was worked out, not what was said, and replaying every historical
-- tool result would grow unboundedly while telling the model things it can
-- look up again more cheaply.
CREATE TABLE IF NOT EXISTS conversations (
    team_id INTEGER PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    gameweek INTEGER,
    turns JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- The ingestion run ledger.
--
-- ADR-0002 decided ingestion is on-demand: no scheduler, nothing runs unless
-- the user asks. That is still true. What it left unanswered is how anyone --
-- the user, or the dashboard -- is supposed to know WHEN it last ran.
--
-- Until this table there was no answer. `gameweek_state.updated_at` and
-- `max(creator_content.fetched_at)` were the only traces a run left, and both
-- are side effects of a run rather than records of one: a run that fetched
-- nothing new leaves fetched_at untouched, and a run that FAILED half way
-- leaves both looking exactly like a run that succeeded. So the dashboard
-- could not say "this data is nine days old", which is the one caveat every
-- panel on it needs. ADR-0015 already commits the dashboard to admitting when
-- it does not know something; it could not honour that about its own
-- freshness.
--
-- A run is recorded regardless of who started it -- the CLI and the HTTP
-- endpoint both go through ingestion/jobs.py -- so `fpl-ingest` in a terminal
-- and the refresh button in the browser produce the same history. Anything
-- else would mean the freshness stamp quietly lying to whichever half of the
-- workflow was not instrumented.
--
-- `status = 'running'` is a claim on the resource, not just a label. The
-- partial unique index below is what makes a double-clicked refresh button a
-- 409 rather than two concurrent Grok bills, and it is the same trick
-- `roadmaps_one_live_per_team` uses.
--
-- Which means a crashed run leaves a 'running' row that would block every
-- future run forever, so a row is reaped as 'abandoned' once it is older than
-- any real run could be (ingestion/runs.py). Deliberately its own status
-- rather than 'failed': we know it stopped being watched, not that it stopped
-- working, and a run that was killed by a uvicorn reload may well have
-- written most of its data.
--
-- `team_id` has NO foreign key, and that is the one non-obvious thing here.
--
-- The very first FPL run is what CREATES the teams row -- so a run recorded
-- against `teams(id)` could not be started until a run had already
-- succeeded, and a fresh database would be permanently unable to ingest
-- anything. A constraint that makes the bootstrap case impossible is worse
-- than no constraint. `creator_stances.fpl_player_id` is unconstrained for a
-- comparable reason.
--
-- Losing the cascade is a bonus rather than a cost: the run history is the
-- audit trail of what touched the data, so it should outlive a wiped team
-- rather than disappear with it.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('fpl', 'creators')),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'abandoned')),
    -- How the run was started. Not used for anything functional; it is here
    -- because "the button did nothing" and "I ran it in a terminal and it
    -- did nothing" are different bug reports.
    trigger TEXT NOT NULL DEFAULT 'api' CHECK (trigger IN ('api', 'cli')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    -- The summary dataclass the pipeline already returns, verbatim. Stored as
    -- JSONB rather than as columns because the two pipelines report entirely
    -- different quantities, both grow a field per ticket, and nothing queries
    -- inside it -- it is rendered, not filtered.
    summary JSONB,
    error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ingestion_runs_one_running_per_kind
    ON ingestion_runs (team_id, kind) WHERE status = 'running';

CREATE INDEX IF NOT EXISTS ingestion_runs_team_kind_started_idx
    ON ingestion_runs (team_id, kind, started_at DESC);
