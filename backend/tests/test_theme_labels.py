"""Which real quote heads a theme.

The panel was opened in a browser for the first time on 2026-09-04 and its
themes were headed by things like "Whereas with now, right, you have this high
floor created for you by the variables discussed beforehand." — a real
sentence, correctly cited, that tells the reader nothing.

The cause was that the cluster's seed and its label were the same chunk. The
seed is chosen for spanning the most creators, which is what makes a theme a
theme; readability is a different question and nothing was asking it.

These are pure-function tests: no clustering, no database, no model. What they
defend is that the label is still always a real member chunk (ADR-0014 — the
label is a quotation, never generated) and that the choice between real
chunks now prefers the readable one.
"""

from fpl_coach.themes import _representative, quotability, theme_title

# Shapes taken from the live corpus rather than invented, so the scoring is
# tuned against what YouTube auto-captions actually produce.
MID_CLAUSE = "in any specific week they're more explosive than"
NAME_LIST = "Bench one, Fodden, Odigard, Brobby, or Cat Lewen."
ABSTRACT = (
    "Whereas with now, right, you have this high floor created for you by the "
    "variables discussed beforehand."
)
INFORMATIVE = (
    "This is my team selection for Gameweek 2. I scored 77 points on bench boost "
    "in Gameweek 1 after deciding to go without Haaland."
)


def _chunk(text: str) -> dict:
    return {"chunk_text": text}


def test_a_mid_clause_fragment_scores_worst():
    """Chunking is done on length, not grammar, so roughly half of all chunks
    begin mid-sentence. Those read as a transcription error rather than as a
    quotation and must lose to anything complete."""
    assert quotability(MID_CLAUSE) < quotability(ABSTRACT)
    assert quotability(MID_CLAUSE) < quotability(INFORMATIVE)


def test_a_read_out_list_of_names_scores_badly():
    """A real sentence and a useless heading. Penalised on comma density
    rather than by looking for names, so it cannot disagree with the players
    list the panel resolves separately through stances."""
    assert quotability(NAME_LIST) < quotability(INFORMATIVE)


def test_a_specific_quote_beats_an_abstract_one():
    assert quotability(INFORMATIVE) > quotability(ABSTRACT)


def test_naming_something_helps():
    """A heading that mentions a proper noun says what the theme is about."""
    named = "The armband case says move it to Haaland this week rather than hold."
    unnamed = "The armband case says move it to the other one this week rather than hold."
    assert quotability(named) > quotability(unnamed)


def test_empty_text_is_not_chosen_over_anything():
    assert quotability("") < quotability(MID_CLAUSE)


def test_the_representative_is_always_a_real_member():
    """The load-bearing property. ADR-0014: a theme's label is a quotation
    with a source_url and a timestamp, never prose a model wrote — so the
    chosen id has to be one of the cluster's own chunks, whatever the
    scoring does."""
    chunks = {1: _chunk(MID_CLAUSE), 2: _chunk(INFORMATIVE), 3: _chunk(NAME_LIST)}
    members = {1, 2, 3}

    assert _representative(1, members, chunks) in members


def test_the_representative_prefers_readability_over_the_seed():
    chunks = {1: _chunk(MID_CLAUSE), 2: _chunk(INFORMATIVE)}

    # Seeded on the fragment, which is what used to head the theme.
    assert _representative(1, {1, 2}, chunks) == 2


def test_the_seed_wins_a_tie():
    """Deterministic, and a cluster whose members all read alike is labelled
    exactly as it was before this change."""
    chunks = {1: _chunk(INFORMATIVE), 2: _chunk(INFORMATIVE)}

    assert _representative(2, {1, 2}, chunks) == 2


def test_a_single_member_cluster_is_labelled_by_that_member():
    """No filtering: every candidate is a real member and one has to be
    chosen. A rule that could reject them all would leave a theme with no
    label, which is worse than a mediocre quote."""
    chunks = {7: _chunk(MID_CLAUSE)}

    assert _representative(7, {7}, chunks) == 7


# ---------------------------------------------------------------------------
# Naming a theme.
#
# `title` was added because a quotation is evidence and evidence does not make
# a heading. What has to hold is that it is still ASSEMBLED rather than
# written: counted terms from a closed vocabulary plus resolved player names,
# and a count when it recognises neither. Nothing here may become prose.


def test_a_topic_term_two_creators_used_names_the_theme():
    texts = [
        "I am not ready to wildcard yet, I will free it and attack",
        "my wildcard is still in my back pocket for gameweek nine",
    ]

    assert theme_title(texts, [], 4) == "Wildcard timing"


def test_the_topic_and_the_players_are_combined():
    texts = ["the double gameweek in nine", "planning around that double gameweek"]

    title = theme_title(texts, ["Haaland", "Palmer"], 3)

    assert title == "Double gameweeks — Haaland, Palmer"


def test_a_term_only_one_content_row_used_is_not_the_theme():
    """One creator's word, not the cluster's subject — the same reason
    MIN_CREATORS is three rather than one."""
    texts = ["I might wildcard", "nothing else about that here", "nor here"]

    assert theme_title(texts, ["Saka"], 3) == "Saka"


def test_the_more_specific_term_wins_a_tie():
    """`_TOPICS` runs most specific first, so a theme about double gameweeks
    is not reported as being about fixtures."""
    texts = [
        "the double gameweek changes the fixtures completely",
        "that double gameweek and its fixtures",
    ]

    assert theme_title(texts, [], 3) == "Double gameweeks"


def test_players_alone_name_a_theme_with_no_recognised_topic():
    texts = ["something nobody has a word for", "likewise"]

    assert theme_title(texts, ["Saka", "Palmer", "Foden", "Wirtz"], 3) == "Saka, Palmer, Foden"


def test_naming_falls_back_to_the_count_rather_than_guessing():
    """STATUS.md §7: when a lookup does not recognise what it was given, say
    so. A title invented from whatever noun happened to be frequent would be
    the fails-open version of this."""
    assert theme_title(["nothing recognisable", "also nothing"], [], 5) == (
        "Discussed by 5 creators"
    )


def test_the_topic_match_is_accent_and_case_insensitive():
    texts = ["Injured again", "another INJURY to report"]

    assert theme_title(texts, [], 3) == "Injuries and fitness"
