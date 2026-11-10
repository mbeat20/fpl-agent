from psycopg import Connection

from fpl_coach.ingestion.fpl_client import FPLClient
from fpl_coach.ingestion.minutes import ensure_player_minutes, recent_minutes
from fpl_coach.models import GameweekMinutes, Player
from fpl_coach.player_names import match_player_name
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.squad_state import gameweeks_played, latest_finished_gameweek, rotation_risk

# Issue 20: minutes/starts ride along -- they are already on fpl_players
# (from the bootstrap payload ingestion fetches anyway), so exposing
# "minutes security" for any pool player costs nothing.
_PLAYER_COLUMNS = (
    "id, name, position, club, price, ownership, form, status, news, xg, xa, "
    "minutes, starts"
)


def player_pool(conn: Connection) -> list[tuple[int, str, str]]:
    """`(id, name, full_name)` for the whole pool, for `match_player_name`.

    Exposed so a caller resolving many names in a row -- creator ingestion
    resolves one per Stance, ~1,600 in a run -- loads the ~650 rows once
    instead of once per name.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, full_name FROM fpl_players")
        return [(row["id"], row["name"], row["full_name"]) for row in cur.fetchall()]


def find_player(
    conn: Connection, name: str, pool: list[tuple[int, str, str]] | None = None
) -> Player | None:
    """Look a name up across the whole FPL player pool (issue 05) -- not just
    the user's squad -- returning None when it is unrecognised or ambiguous.

    Matching itself lives in `player_names.match_player_name`, which explains
    the tiers and why an ambiguous name resolves to None. The short version is
    that FPL's two stored names (`B.Fernandes`, `Bruno Borges Fernandes`) are
    both unlike what a creator says ("Bruno Fernandes"), so token comparison
    replaces the old exact-then-`%substring%` pair.

    The substring fallback that used to sit here is gone rather than kept as a
    last resort. `... ILIKE %name% ORDER BY name LIMIT 1` answered an
    ambiguous name by returning whichever player sorted first -- the fail-open
    shape of STATUS.md §7 -- and it is the reason "Sanchez" could land on
    Juanlu rather than Robert. Tier 3 covers the case it was written for
    ("Salah" finding "Mohamed Salah") and abstains where it cannot be sure.

    `pool` is an optional pre-loaded `player_pool(conn)`; omitted, it is
    fetched.
    """
    matched = match_player_name(name, player_pool(conn) if pool is None else pool)
    if matched is None:
        return None
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_PLAYER_COLUMNS} FROM fpl_players WHERE id = %s", (matched,))
        row = cur.fetchone()
    return Player(**row) if row is not None else None


def get_player(conn: Connection, name: str, client: FPLClient | None = None) -> Player:
    """Issue 20: when a client is supplied, this is also the on-demand half of
    the minutes cache -- the user naming a player is exactly the signal that
    his per-fixture history is worth one element-summary call. Scoped to that
    one player, never the pool, and a no-op when the cache is already current.

    Without a client it degrades to season totals only, which is what every
    pre-existing caller (and every test that does not want an HTTP boundary)
    relies on."""
    player = find_player(conn, name)
    if player is None:
        raise ValueError(f"No FPL player found matching {name!r}")
    player.rotation_risk = rotation_risk(
        player.minutes, gameweeks_played(conn), player.status
    )
    if client is not None:
        ensure_player_minutes(conn, client, player.id, latest_finished_gameweek(conn))
        player.recent_minutes = [
            GameweekMinutes(**row) for row in recent_minutes(conn, player.id)
        ]
    return player


def make_get_player_tool(conn: Connection, client: FPLClient | None = None) -> ToolSpec:
    return ToolSpec(
        name="get_player",
        description=(
            "Looks up any FPL player by name (not just the user's squad) — "
            "id, position, club, price, ownership %, form, injury status, and "
            "season-to-date xG/xA (FPL's official Opta figures; null only if not "
            "yet ingested), plus season "
            "minutes and starts. rotation_risk is true for an AVAILABLE player "
            "averaging under 60 minutes a gameweek -- a real risk even with no "
            "injury news, and worth saying out loud before recommending a transfer "
            "in. Low minutes on an injured player are already explained by status "
            "and are not flagged here. recent_minutes gives the last few gameweeks "
            "individually, newest first -- read it before calling anyone nailed, "
            "because 180 season minutes is a regular starter who missed two weeks or "
            "a substitute playing 20 a week, and those are opposite transfer cases."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "player name -- a surname on its own is fine, but spell "
                        "whole words: a truncated name, or a surname two players "
                        "share (Palmer, James, Henderson), is rejected rather "
                        "than guessed at. Add the forename to disambiguate."
                    ),
                }
            },
            "required": ["name"],
        },
        handler=lambda name: get_player(conn, name, client),
    )
