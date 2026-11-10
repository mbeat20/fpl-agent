"""Ticket 29: a Stance's `subject` reaches the database under one spelling.

Pure -- the club list is passed in. These are FPL's real club names, as
`clubs` stores them.
"""

import pytest

from fpl_coach.subjects import normalise_subject

CLUBS = [
    ("Arsenal", "ARS"),
    ("Aston Villa", "AVL"),
    ("Brentford", "BRE"),
    ("Brighton", "BHA"),
    ("Chelsea", "CHE"),
    ("Coventry City", "COV"),
    ("Hull City", "HUL"),
    ("Leeds", "LEE"),
    ("Man City", "MCI"),
    ("Man Utd", "MUN"),
    ("Newcastle", "NEW"),
    ("Nott'm Forest", "NFO"),
    ("Spurs", "TOT"),
]


class TestChips:
    @pytest.mark.parametrize(
        "written", ["wildcard", "wild card", "Wildcard", "WILD CARD", "wild_card", " Wildcard "]
    )
    def test_every_spelling_of_one_chip_collapses(self, written):
        """On the live corpus this one chip arrived as `wildcard` (25),
        `wild card` (7) and `Wildcard` (2) -- three subjects, so ticket 19
        counted one consensus as three weak ones."""
        assert normalise_subject(written, CLUBS) == "wildcard"

    @pytest.mark.parametrize(
        "written,expected",
        [
            ("bench boost", "bench boost"),
            ("Bench Boost", "bench boost"),
            ("bench_boost", "bench boost"),
            ("bboost", "bench boost"),
            ("triple captain", "triple captain"),
            ("Triple Captain", "triple captain"),
            ("triple_captain", "triple captain"),
            ("3xc", "triple captain"),
            ("free hit", "free hit"),
            ("Free Hit", "free hit"),
            ("free_hit", "free hit"),
        ],
    )
    def test_the_rest_of_the_chip_vocabulary(self, written, expected):
        assert normalise_subject(written, CLUBS) == expected

    def test_fpls_own_api_spellings_are_accepted(self):
        """`chips_used` stores `3xc` and `bboost`; no creator says either, but
        both must land on the same subject a creator's words do."""
        assert normalise_subject("3xc", CLUBS) == normalise_subject("Triple Captain", CLUBS)


class TestClubs:
    @pytest.mark.parametrize(
        "written,expected",
        [
            ("Manchester City", "Man City"),
            ("Manchester United", "Man Utd"),
            ("Tottenham", "Spurs"),
            ("Nottingham Forest", "Nott'm Forest"),
        ],
    )
    def test_a_common_name_that_shares_no_prefix_with_fpls(self, written, expected):
        assert normalise_subject(written, CLUBS) == expected

    def test_the_creator_drops_the_suffix(self):
        assert normalise_subject("Coventry", CLUBS) == "Coventry City"
        assert normalise_subject("Hull", CLUBS) == "Hull City"

    def test_the_creator_adds_a_suffix(self):
        assert normalise_subject("Newcastle United", CLUBS) == "Newcastle"

    def test_a_short_name(self):
        assert normalise_subject("NFO", CLUBS) == "Nott'm Forest"

    def test_a_name_already_canonical_is_returned_as_is(self):
        assert normalise_subject("Arsenal", CLUBS) == "Arsenal"


class TestLeavesAlone:
    def test_a_typo_is_not_repaired(self):
        """`Brenford` appeared 3 times in the corpus. Merging it into
        Brentford needs a fuzzy threshold, and one loose enough to catch a
        dropped `t` is loose enough to merge two real clubs -- the same
        reasoning as `player_names`."""
        assert normalise_subject("Brenford", CLUBS) == "Brenford"

    def test_an_ambiguous_prefix_abstains(self):
        """A bare `Man` reaches both Man City and Man Utd."""
        assert normalise_subject("Man", CLUBS) == "Man"

    @pytest.mark.parametrize("other", ["transfer", "free transfer", "Commentary", "price rises"])
    def test_a_subject_that_is_neither_chip_nor_club(self, other):
        """Returned verbatim rather than dropped: an unrecognised subject is
        still a real subject, and `creator_stances_subject_check` requires a
        player-less Stance to have one."""
        assert normalise_subject(other, CLUBS) == other

    def test_none_stays_none(self):
        assert normalise_subject(None, CLUBS) is None

    def test_blank_is_not_turned_into_something(self):
        assert normalise_subject("   ", CLUBS) == "   "


def test_normalisation_is_idempotent():
    """Its own output must be a fixed point, or the migration script changes
    rows every time it runs."""
    for written in ["wild card", "Manchester City", "Coventry", "Brenford", "transfer"]:
        once = normalise_subject(written, CLUBS)
        assert normalise_subject(once, CLUBS) == once
