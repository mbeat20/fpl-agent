"""Unit tests for the pure name matcher. No database -- the candidate pool is
passed in, so every case here is a real (id, name, full_name) triple copied
from the live `fpl_players` table.
"""

import pytest

from fpl_coach.player_names import fold, match_player_name, tokens

# Real rows from the pool, chosen because each one broke the old
# exact-then-substring lookup or is a near neighbour that must NOT be matched.
POOL = [
    (426, "B.Fernandes", "Bruno Borges Fernandes"),
    (525, "Fernandes", "Mateus Fernandes"),
    (634, "Fernandez-Pardo", "Matias Fernandez-Pardo"),
    (10, "White", "Benjamin White"),
    (480, "Gibbs-White", "Morgan Gibbs-White"),
    (140, "Sánchez", "Robert Lynch Sánchez"),
    (573, "Sanchez", "Juanlu Sánchez"),
    (411, "Haaland", "Erling Haaland"),
    (469, "N.Williams", "Neco Williams"),
    (601, "Williams-Barnett", "Luca Williams-Barnett"),
    (18, "Martinelli", "Gabriel Martinelli Silva"),
    (391, "Gvardiol", "Josko Gvardiol"),
    (496, "Kinsky", "Antonin Kinsky"),
    (1001, "Groß", "Pascal Groß"),
]


class TestFoldAndTokens:
    def test_strips_diacritics(self):
        assert fold("Sánchez") == "sanchez"
        assert fold("Vinícius Júnior") == "vinicius junior"

    def test_eszett_becomes_ss(self):
        """NFD leaves ß alone, so without the explicit mapping a creator
        writing the anglicised "Gross" never reaches FPL's "Groß"."""
        assert fold("Groß") == "gross"
        assert fold("Groß") == fold("Gross")

    def test_splits_on_punctuation_not_just_whitespace(self):
        """The two FPL name shapes only become comparable once the initial's
        dot and the double-barrel's hyphen are separators."""
        assert tokens("B.Fernandes") == ("b", "fernandes")
        assert tokens("Gibbs-White") == ("gibbs", "white")
        assert tokens("O'Riley") == ("o", "riley")


class TestExactTier:
    def test_web_name(self):
        assert match_player_name("Haaland", POOL) == 411

    def test_full_name(self):
        assert match_player_name("Erling Haaland", POOL) == 411

    def test_exact_match_ignores_accents(self):
        assert match_player_name("Josko Gvardiol", POOL) == 391


class TestSurnamePlusForename:
    def test_full_name_with_a_middle_name_in_between(self):
        """The case that motivated the module: FPL stores B.Fernandes /
        Bruno Borges Fernandes, so "Bruno Fernandes" matched neither exactly
        nor as a substring, and 42 stances about the user's own captain were
        left unresolved."""
        assert match_player_name("Bruno Fernandes", POOL) == 426

    def test_short_forename_matches_fpls_long_one(self):
        assert match_player_name("Ben White", POOL) == 10

    def test_forename_separates_two_players_sharing_a_surname(self):
        """Both Sanchezes answer to the surname; only the forename picks one,
        and only after accents are folded."""
        assert match_player_name("Robert Sanchez", POOL) == 140
        assert match_player_name("Juanlu Sanchez", POOL) == 573

    def test_anglicised_spelling(self):
        assert match_player_name("Pascal Gross", POOL) == 1001

    def test_surname_must_be_the_candidates_last_token(self):
        """Gabriel Martinelli Silva ends in Silva, so a bare "Silva" is not
        him under tier 3 -- but "Gabriel Martinelli" is, on the forename."""
        assert match_player_name("Gabriel Martinelli", POOL) == 18


class TestAbstains:
    def test_forename_agreeing_with_nobody_does_not_fall_through_to_surname(self):
        """The trap this module exists to avoid. No Fernandez in this pool has
        the forename Enzo, and the nearest surname is Matias Fernandez-Pardo.
        Attributing the stance to him would put an opinion the creator never
        held into a trust record and let ticket 19 count it.

        (The real pool does contain an Enzo, stored as `Enzo` / `Enzo
        Fernández` -- see the paired test below. He is left out here so the
        fall-through guard itself is what is under test.)"""
        assert match_player_name("Enzo Fernandez", POOL) is None

    def test_the_same_query_resolves_when_that_player_is_present(self):
        """The guard abstains for want of a candidate, not by blacklisting a
        spelling -- with Enzo in the pool the accent-folded surname and the
        forename both agree, and Fernandez-Pardo still loses on surname."""
        pool = [*POOL, (700, "Enzo", "Enzo Fernández")]
        assert match_player_name("Enzo Fernandez", pool) == 700

    def test_misspelt_forename_is_not_coerced(self):
        assert match_player_name("Bruno Fernandez", POOL) is None

    def test_player_not_in_the_pool(self):
        """58% of unmatched names are this case -- creators discussing players
        abroad or out of the league. Staying unmatched is correct."""
        assert match_player_name("Kylian Mbappe", POOL) is None

    def test_different_forename_same_surname(self):
        """Nico Williams (Athletic Bilbao) is not Neco Williams (Forest)."""
        assert match_player_name("Nico Williams", POOL) is None

    def test_ambiguous_bare_surname_returns_none_rather_than_first_alphabetically(self):
        """The old `ILIKE %name% ORDER BY name LIMIT 1` answered this by
        returning whichever player sorted first. Two players answer to
        "Fernandes" on their own, so there is no safe answer."""
        assert match_player_name("Fernandes", POOL) is None

    def test_a_bare_surname_two_real_players_share(self):
        """Not a contrived case: the live pool has Cole *and* Alex Palmer,
        Reece *and* Daniel James, Jordan *and* Dean Henderson -- each pair
        sharing one web name. The old lookup broke those ties by sort order,
        so ten "Palmer" stances were attributed by coin flip."""
        pool = [(200, "Palmer", "Cole Palmer"), (201, "Palmer", "Alex Palmer")]
        assert match_player_name("Palmer", pool) is None
        assert match_player_name("Cole Palmer", pool) == 200

    def test_transcript_mishearing_is_not_fuzzy_matched(self):
        """"Kinski" for Kinsky and "Guardial" for Gvardiol are one or two
        characters from a real player, and so are plenty of genuinely
        different players. Deliberately not recovered."""
        assert match_player_name("Kinski", POOL) is None
        assert match_player_name("Guardial", POOL) is None

    @pytest.mark.parametrize("empty", ["", "   ", "..."])
    def test_empty_query(self, empty):
        assert match_player_name(empty, POOL) is None
