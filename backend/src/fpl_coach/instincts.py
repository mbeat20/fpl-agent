"""Issue 13: an Instinct is the user's own early-week transfer/captaincy
note, gameweek-scoped -- distinct from preferences.py's standing
Preferences despite sharing the "remember: X" phrasing (CONTEXT.md).
"""

import re

from psycopg import Connection

_INSTINCT_RE = re.compile(r"^\s*thinking\b", re.IGNORECASE)


def is_instinct(remembered_text: str) -> bool:
    """True when a "remember: X" instruction's X reads as a dated Instinct
    ("thinking Rogers to Enzo") rather than a standing Preference. The
    "thinking" marker is the same phrasing CONTEXT.md itself uses for an
    Instinct -- deterministic and regex-based, matching ADR-0010's "no
    extraction pipeline" spirit rather than asking Claude to classify."""
    return bool(_INSTINCT_RE.match(remembered_text))


def current_gameweek(conn: Connection, team_id: int) -> int | None:
    """`None` until run_ingestion has resolved and persisted a gameweek for
    this team (gameweek_state, keyed by team_id so a fake test ingestion can
    never clobber the real team's row)."""
    with conn.cursor() as cur:
        cur.execute("SELECT gameweek FROM gameweek_state WHERE team_id = %s", (team_id,))
        row = cur.fetchone()
    return row["gameweek"] if row else None


def log_instinct(conn: Connection, team_id: int, text: str) -> None:
    """Tagged with the *planning* gameweek, not the persisted one. An
    Instinct is by definition a pre-deadline note about the decision the user
    can still act on, so tagging it with gameweek_state's snapshot -- which
    still names the finished gameweek until the next one kicks off -- files
    it under a gameweek whose deadline has already passed, where
    get_instincts will never surface it again.

    Imported inside the function: gameweek.py imports current_gameweek from
    this module, so a module-level import here would be circular."""
    from fpl_coach.gameweek import planning_gameweek

    gameweek = planning_gameweek(conn, team_id)
    if gameweek is None:
        raise ValueError(
            "No gameweek known for this team yet -- run ingestion before logging an Instinct"
        )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
            (team_id, gameweek, text),
        )
    conn.commit()


# Issue 19: an Instinct is free text ("thinking Rogers to Enzo") -- ticket 13
# deliberately gave it no fpl_player_id, because it is the user's own note and
# not a structured pick. The dashboard's consensus panel still has to know
# which players a note is about, so the names are recovered by matching the
# text against the ingested player pool rather than by asking the user to
# structure it or asking Claude to parse it (ADR-0010's "no extraction
# pipeline" spirit, and this path must stay free of billed calls).
#
# Word-boundary and case-insensitive, against `name` (FPL's web_name, which is
# what a creator or a manager actually types) and never against `full_name` --
# matching first names would fire on half the pool.
_INSTINCT_PLAYER_SQL = r"""
SELECT DISTINCT name
FROM fpl_players
WHERE %s ~* ('\m' || regexp_replace(name, '([\\^$.|?*+()\[\]{}])', '\\\1', 'g') || '\M')
ORDER BY name
"""


def players_named_in(conn: Connection, text: str) -> list[str]:
    """Player names from the pool that appear as whole words in `text`.

    Regex-escaped rather than interpolated raw: real FPL web_names contain
    characters that are regex metacharacters (`Kovačić` is fine, but names
    with a `.` such as `B.Fernandes` are not), and an unescaped `.` would
    match any character and produce silent false positives.
    """
    if not text.strip():
        return []
    with conn.cursor() as cur:
        cur.execute(_INSTINCT_PLAYER_SQL, (text,))
        return [row["name"] for row in cur.fetchall()]


def instinct_players(conn: Connection, team_id: int) -> list[tuple[str, str]]:
    """[(player_name, the instinct text that named them)] for this gameweek's
    live Instincts. One row per (player, note) pair, so a note naming two
    players -- the usual shape, since an Instinct is normally a swap --
    yields both."""
    from fpl_coach.tools.get_instincts import get_instincts

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for note in get_instincts(conn, team_id).notes:
        for name in players_named_in(conn, note.text):
            if name in seen:
                continue
            seen.add(name)
            pairs.append((name, note.text))
    return pairs
