"""Matching a name a creator said to a player in the FPL pool.

Pure functions, no database -- the DB-facing caller is
`fpl_coach.tools.get_player.find_player`, same split as `consensus.py` /
`get_creator_consensus.py`.

The problem this exists to solve: FPL publishes two names per player and
neither is the one a creator says out loud. `name` is a web name, often an
initialled abbreviation (`B.Fernandes`, `N.Williams`), and `full_name` carries
middle names the player is never called by (`Bruno Borges Fernandes`). A
creator saying "Bruno Fernandes" matches neither exactly, and does not match
`%Bruno Fernandes%` either, because "Borges" sits in the middle. Before this
module 44% of player-named Creator Stances resolved to no `fpl_player_id`, and
under ADR-0004 -- where a pick is a lookup by recorded id, never prose -- an
unmatched Stance can never reach a Recommendation. 42 of them were about the
user's own captain.

The tiers below are ordered strict-to-loose and stop at the first tier that
matches anything. **A tier that matches more than one player returns None
rather than picking one.** That is STATUS.md §7's rule -- when a lookup does
not recognise what it was given, say so; never fall back to something that
looks like an answer. Concretely, `Enzo Fernandez` is not in the pool, and the
nearest surname is Matias `Fernandez-Pardo`. Attributing an Enzo Fernandez
stance to Fernandez-Pardo would put an opinion the creator never held into a
trust record and let ticket 19 count it, which is strictly worse than leaving
the row unmatched.

Not attempted, deliberately: fuzzy/edit-distance matching. `Kinski` for
`Kinsky` and `Guardial` for `Gvardiol` are auto-transcript mishearings one or
two characters away from a real player, and so are plenty of genuinely
different players. A threshold loose enough to catch them is loose enough to
invent attributions.

That was measured on 2026-09-04 and holds: of 491 unmatched Stances only 16%
even have a phonetically unique candidate, and about half of those candidates
are the wrong player (`Savinho` -> Savona, `Nico Williams` -> Neco Williams,
`Jhon Duran` -> Alcaraz Duran). The full numbers are recorded next to
`_FORENAME_VARIANTS` below, so the question does not have to be re-opened from
scratch.

What IS attempted, because both are facts about names rather than guesses
about strings: a curated table of given-name variants (`Andy`/`Andrew`,
`Ryan`/`Rayan`), and surname particles (`Le Fee`) no longer being mistaken for
forenames.
"""

import re
import unicodedata

__all__ = ["fold", "tokens", "match_player_name"]

# ß has no combining-mark decomposition, so NFD leaves it alone and a creator
# writing the anglicised "Gross" never reaches "Groß". Handled before NFD
# because the mapping is one-to-two characters.
_PRE_FOLD = str.maketrans({"ß": "ss", "Ø": "o", "ø": "o", "Đ": "d", "đ": "d", "ł": "l", "Ł": "l"})

_NON_WORD = re.compile(r"[^0-9a-z]+")

# How much of a forename both sides must spell out before a prefix comparison
# is allowed to count. See `_forename_agrees`.
_MIN_FORENAME_CHARS = 3

# ---------------------------------------------------------------------------
# Two curated tables, added 2026-09-04 after measuring the corpus.
#
# THE MEASUREMENT, because it is the reason there is nothing more here. 491
# player-named Stances were unmatched. Bucketed by whether the surname
# collapses to the same consonant skeleton as exactly one pool player:
#
#   34%  no pool match at all      -- real players who are not in the PL
#   25%  skeleton too short to code safely
#   24%  phonetically ambiguous    -- correctly abstained
#   16%  phonetically unique       -- the only bucket a repair could act on
#
# And roughly HALF of that last 16% would be a misattribution: `Savinho` ->
# Savona, `Vinicius Junior` -> Iling-Junior, `Nico Williams` -> Neco Williams,
# `Jhon Duran` -> Alcaraz Duran, `Arne Slot` (a manager) -> Seelt. Narrowed
# further to "surname exact AND unique in the pool, forename disagreed", the
# forename veto turns out to be right about 2.5 times for every time it is
# wrong. The module docstring's refusal to do edit-distance matching was
# correct and is now correct with numbers attached.
#
# So what is below is deliberately NOT fuzzy. Both tables are finite, curated
# and reviewable, and each entry is a fact about names rather than a guess
# about a string.

# Given names that are the same name. Diminutives, and transliteration
# variants of one name -- never two different names that merely look alike.
# `nico`/`neco` is the case this must NOT contain: they are two people.
_FORENAME_VARIANTS: dict[str, str] = {
    "andy": "andrew",
    "drew": "andrew",
    "bobby": "robert",
    "rob": "robert",
    "robbie": "robert",
    "tommy": "thomas",
    "tom": "thomas",
    "nicholas": "nicolas",
    "nick": "nicolas",
    "matty": "matthew",
    "matt": "matthew",
    "joe": "joseph",
    "jim": "james",
    "jamie": "james",
    "jack": "john",
    "harry": "harold",
    "billy": "william",
    "will": "william",
    "alex": "alexander",
    "sasha": "alexander",
    "mo": "mohamed",
    "mohammed": "mohamed",
    "muhamed": "mohamed",
    "rayan": "ryan",
    "enso": "enzo",
    "igho": "igor",
    "dom": "dominic",
    "ben": "benjamin",
    "danny": "daniel",
    "dan": "daniel",
    "mike": "michael",
    "micky": "michael",
    "chris": "christopher",
    "kris": "christopher",
    "pep": "josep",
}

# Surname particles. Not forenames, and treating them as one is why
# `Le Fee` failed to reach Enzo `Le Fee`: tokens() yields ('le', 'fee'), so
# 'fee' became the surname and 'le' became the only forename on offer -- which
# is two characters long and so could never satisfy _MIN_FORENAME_CHARS.
#
# Dropped from the forename list rather than glued onto the surname. Gluing
# would mean deciding whether the pool spells it `Le Fee`, `LeFee` or `Fee`,
# and the pool is inconsistent about exactly that; dropping them just stops a
# particle being mistaken for evidence about a given name.
_PARTICLES = frozenset(
    {"le", "la", "de", "del", "della", "di", "da", "das", "dos", "du",
     "van", "von", "der", "den", "ter", "al", "el", "bin", "ibn", "mac", "mc"}
)


def fold(text: str) -> str:
    """Casefold and strip diacritics, so `Sánchez` and `Sanchez` are one name.

    Accents are the single largest recoverable class after the web-name
    problem: `Luis Díaz`, `Raúl Jiménez`, `Robert Sánchez`, `Vinícius Júnior`
    and `Gabriel Magalhães` all appear in creator prose with and without them,
    sometimes in the same transcript.
    """
    text = text.translate(_PRE_FOLD)
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def tokens(name: str) -> tuple[str, ...]:
    """Fold, then split on anything that is not a letter or digit.

    Splitting on punctuation rather than whitespace alone is what makes the
    two FPL name shapes comparable: `B.Fernandes` becomes `('b',
    'fernandes')` and `Gibbs-White` becomes `('gibbs', 'white')`, so both can
    be compared token-wise against what a creator actually said.
    """
    return tuple(part for part in _NON_WORD.split(fold(name)) if part)


def _forename_agrees(query_forenames: tuple[str, ...], pool_tokens: tuple[str, ...]) -> bool:
    """Does at least one of the creator's leading tokens name this player?

    Prefix rather than equality, in both directions, because the short form
    can be on either side: a creator's `Ben White` against FPL's `Benjamin
    White`, or a creator's full `Bruno` against a stored `Bruno Borges`.

    **A bare initial never matches a spelled-out forename.** Allowing it made
    `Nico Williams` match Neco `Williams`, whose web name is `N.Williams` --
    the initial `n` is a prefix of `nico`, so the one token that was supposed
    to tell the two players apart agreed with both. An initial carries about
    four bits of evidence and this tier needs the forename to be doing real
    work, so both sides must be spelled out far enough to disagree.

    Requiring agreement at all is what keeps tier 2 safe: `Ben White` reaches
    both Benjamin `White` and Morgan `Gibbs-White` on surname alone.
    """
    query_given = _given_names(query_forenames)
    pool_given = _given_names(pool_tokens)
    return any(
        min(len(query_token), len(pool_token)) >= _MIN_FORENAME_CHARS
        and (pool_token.startswith(query_token) or query_token.startswith(pool_token))
        for query_token in query_given
        for pool_token in pool_given
    )


def _given_names(name_tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Tokens that are actually given names, canonicalised.

    Two jobs, both narrow. Particles are dropped, because `le` in `Le Fee` is
    part of a surname and carries no evidence about a forename. Known variants
    of one name are folded onto a single spelling, so a creator's `Andy
    Robertson` reaches FPL's `Andrew Robertson` and `Ryan Cherki` reaches
    `Rayan Cherki` -- both of which the prefix comparison alone rejects, since
    neither string is a prefix of the other.

    Applied to BOTH sides. The pool's own spelling is the arbitrary one as
    often as the creator's, and mapping only the query would leave a stored
    `Matty Cash` unreachable from a creator's `Matthew`.
    """
    return tuple(
        _FORENAME_VARIANTS.get(token, token)
        for token in name_tokens
        if token not in _PARTICLES
    )


def match_player_name(query: str, candidates: list[tuple[int, str, str]]) -> int | None:
    """Resolve `query` to one candidate's id, or None if it is unrecognised or
    ambiguous.

    `candidates` is `(id, name, full_name)` for the FPL pool -- passed in
    rather than queried so this stays pure and so ingestion can load the pool
    once for a whole run instead of once per Stance.

    None is a real and common answer, not a failure: 58% of the names left
    unmatched on the corpus this was measured against are players who are not
    in the pool at all -- creators discussing transfer targets abroad, or
    players who left the league. Those *should* stay unmatched.
    """
    query_tokens = tokens(query)
    if not query_tokens:
        return None

    surname = query_tokens[-1]
    forenames = query_tokens[:-1]

    # A query ending in a particle ("van", "de") is a truncated name, not a
    # surname -- matching on it would compare the wrong token entirely.
    if surname in _PARTICLES:
        return None

    # A query whose only leading tokens were particles (`Le Fee`) offers no
    # forename evidence at all, so it is a one-word query in every sense that
    # matters here. Routed to tier 3 rather than left to fail tier 2 against
    # an empty forename list, which is what made `Le Fee` unmatchable while
    # `Enso Le Fee` matched.
    if forenames and not _given_names(forenames):
        forenames = ()

    # A one-word query goes straight to tier 3, skipping the exact tier.
    #
    # This looks like throwing away a strong signal and is not. FPL's web
    # names are internally disambiguated -- Mateus Fernandes is `Fernandes`
    # precisely because Bruno was already `B.Fernandes` -- so a bare
    # "Fernandes" matches Mateus's web name exactly while overwhelmingly
    # meaning Bruno in any FPL conversation. Letting the exact tier answer it
    # returns a confident wrong player; tier 3 sees both and abstains. Only a
    # query that offers a forename gets to use the exact tier's confidence.
    if not forenames:
        return _unique_by_surname(surname, candidates)

    folded = fold(query).strip()
    exact = {
        pid
        for pid, name, full_name in candidates
        if fold(name).strip() == folded or fold(full_name).strip() == folded
    }
    if exact:
        return exact.pop() if len(exact) == 1 else None

    # Tier 2: surname agreement plus forename agreement. The surname must be
    # the candidate's *last* token, not merely present: `Fernandez` appearing
    # first in `Fernandez-Pardo` is a different family name, not a match.
    #
    # The two conditions are checked against the web name and the full name
    # independently, so evidence may come from either. Gabriel Martinelli is
    # stored as `Martinelli` / `Gabriel Martinelli Silva`: the surname matches
    # the web name and the forename matches the full name, and neither name on
    # its own would do.
    tier2 = {
        pid
        for pid, name, full_name in candidates
        for pool_names in ((tokens(name), tokens(full_name)),)
        if any(t and t[-1] == surname for t in pool_names)
        and any(_forename_agrees(forenames, t) for t in pool_names)
    }
    if tier2:
        return tier2.pop() if len(tier2) == 1 else None

    # A query that offered a forename and still reached no candidate stops
    # here rather than falling through to a surname-only match. That fall-
    # through is exactly how `Enzo Fernandez` would become Matias
    # `Fernandez-Pardo`: the creator gave a forename, it agreed with nobody,
    # and that is evidence *against* a match rather than noise to discard.
    return None


def _unique_by_surname(surname: str, candidates: list[tuple[int, str, str]]) -> int | None:
    """Tier 3 -- the creator said one word (`Haaland`, `Okafor`, `Martinelli`).

    Nothing was offered to disambiguate with, so the match is only safe when
    exactly one player in the pool answers to that final token. `Silva`
    reaches both Gabriel Martinelli Silva and António ... da Silva and so
    resolves to neither.
    """
    matches = {
        pid
        for pid, name, full_name in candidates
        if any(t and t[-1] == surname for t in (tokens(name), tokens(full_name)))
    }
    return matches.pop() if len(matches) == 1 else None
