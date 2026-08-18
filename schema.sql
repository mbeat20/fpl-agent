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
-- creator_content -- one piece of content may yield multiple Stances.
-- Embedding creator_content into pgvector (ADR-0005, ticket 09's semantic
-- search) is deferred until VOYAGE_API_KEY is available; this table only
-- covers the structured half.
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
