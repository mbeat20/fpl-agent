"""Normalising a Creator Stance's `subject` to one canonical spelling.

Pure. The DB-facing callers are `_store_stances` (at extraction time) and
`scripts/normalise_subjects.py` (for rows already stored).

CONTEXT.md: a Stance can be about no player at all -- when to play a chip, or
how good a club's fixture run is -- in which case it names its `subject`.
Nothing constrained what went in that column, so the extractor wrote whatever
the creator said, and on the live corpus one idea reached the database under
as many as three spellings:

    wildcard 25 | wild card 7 | Wildcard 2      -- one chip, three rows
    free hit 13 | free_hit 3  | Free Hit 2
    Manchester City 8 | Man City 3              -- one club, two rows

Ticket 19 counts *distinct creators holding a view*, so a split subject splits
the count, and a chip six creators agree on can present as three weak
opinions. Ticket 29's theme clustering has the same problem one layer up.

Two closed vocabularies do the work, and nothing else is touched:

  chips  -- a fixed set FPL itself defines. Case, spacing and underscores
            collapse; there is no way to be wrong about what `wild_card`
            means.
  clubs  -- resolved against the `clubs` table, which ingestion already
            populates from bootstrap. An authoritative lookup, the same shape
            ADR-0004 requires for a pick.

Anything else -- `transfer`, `Commentary`, a manager's name -- is returned
unchanged. And, exactly as in `player_names`, a near miss is *not* repaired:
`Brenford` stays `Brenford` rather than becoming Brentford, because a
threshold loose enough to catch a typo is loose enough to merge two real
clubs.
"""

from fpl_coach.player_names import fold

__all__ = ["CHIPS", "normalise_subject"]

# FPL's own chip names, in the spelling FPL uses. The key is the folded form
# with separators stripped, so `wild card`, `wild_card`, `Wildcard` and
# `WILDCARD` all arrive at the same entry.
CHIPS = {
    "wildcard": "wildcard",
    "benchboost": "bench boost",
    "triplecaptain": "triple captain",
    "freehit": "free hit",
    "assistantmanager": "assistant manager",
    # FPL's API spells these without spaces in `chips_used`; creators do not.
    "3xc": "triple captain",
    "bboost": "bench boost",
}


def _squash(text: str) -> str:
    """Folded, with every separator removed -- `Wild Card` -> `wildcard`."""
    return "".join(ch for ch in fold(text) if ch.isalnum())


def normalise_subject(subject: str | None, clubs: list[tuple[str, str]]) -> str | None:
    """Canonical spelling for `subject`, or the input unchanged when it names
    neither a chip nor a club.

    `clubs` is `(name, short_name)` from the `clubs` table -- passed in rather
    than queried, so this stays pure and a caller normalising many rows loads
    the 20 rows once.

    Returns the input verbatim (not None) when nothing matches: an
    unrecognised subject is still a real subject, and blanking it would lose a
    Stance that `creator_stances_subject_check` requires to have one.
    """
    if subject is None:
        return None
    stripped = subject.strip()
    if not stripped:
        return subject

    squashed = _squash(stripped)
    if squashed in CHIPS:
        return CHIPS[squashed]

    club = _resolve_club(stripped, clubs)
    return club if club is not None else stripped


def _resolve_club(subject: str, clubs: list[tuple[str, str]]) -> str | None:
    """The club's FPL `name`, or None.

    Exact on either name or short name first. Then a prefix relation in either
    direction, which is what carries the two shapes creators actually use:
    `Coventry` for `Coventry City` (the creator drops the suffix) and
    `Newcastle United` for `Newcastle` (the creator adds it). Ambiguity
    abstains -- a bare `Man` reaches both Man City and Man Utd.

    A prefix relation is deliberately weaker evidence than it looks, so it is
    only consulted after exact matching has failed for every club. Without
    that ordering `Leeds` could be captured by a longer club that happens to
    start with it.
    """
    folded = fold(subject)
    exact = {name for name, short in clubs if fold(name) == folded or fold(short) == folded}
    if len(exact) == 1:
        return exact.pop()
    if exact:
        return None

    aliases = _ALIASES.get(folded)
    if aliases is not None:
        matched = {name for name, _ in clubs if fold(name) == aliases}
        if len(matched) == 1:
            return matched.pop()
        return None

    prefixed = {
        name
        for name, _ in clubs
        if fold(name).startswith(folded) or folded.startswith(fold(name))
    }
    return prefixed.pop() if len(prefixed) == 1 else None


# The clubs whose common name shares no prefix with FPL's, so no rule derives
# them. Kept explicit and short rather than inferred: each line is a fact
# about English football, not a pattern.
_ALIASES = {
    "manchester city": "man city",
    "manchester utd": "man utd",
    "manchester united": "man utd",
    "man united": "man utd",
    "tottenham": "spurs",
    "tottenham hotspur": "spurs",
    "nottingham forest": "nott'm forest",
    "notts forest": "nott'm forest",
    "forest": "nott'm forest",
    "wolverhampton": "wolves",
    "wolverhampton wanderers": "wolves",
    "brighton and hove albion": "brighton",
    "west ham united": "west ham",
    "leeds united": "leeds",
}


def club_names(conn) -> list[tuple[str, str]]:
    """`(name, short_name)` for every club, for `normalise_subject`.

    Ingestion refreshes this table from bootstrap every cycle, so it is the
    authoritative spelling of a club -- the same role `fpl_players` plays for
    a player in `player_names`.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT name, short_name FROM clubs")
        return [(row["name"], row["short_name"]) for row in cur.fetchall()]
