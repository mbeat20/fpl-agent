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
