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
    gameweek = current_gameweek(conn, team_id)
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
