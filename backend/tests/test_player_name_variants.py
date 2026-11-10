"""Given-name variants and surname particles.

Added 2026-09-04 after measuring why 33% of player-named Stances resolve to no
`fpl_player_id`. The headline of that measurement is that the existing matcher
is mostly right and a fuzzy pass would make things worse -- of 491 unmatched
Stances only 16% have a phonetically unique candidate, and about half of those
candidates are the wrong player.

Two classes were genuinely fixable without guessing, and this file is the line
between them and the ones that must stay unmatched. The second half matters
more than the first: every `must abstain` case below is a real pair of
different people, and a change that makes one of them pass has put an opinion
a creator never held into the trust record and let ticket 19 count it.
"""

import pytest

from fpl_coach.player_names import match_player_name

# (id, name, full_name) -- the two shapes FPL publishes, as
# `match_player_name` receives them.
POOL = [
    (1, "Robertson", "Andrew Robertson"),
    (2, "Cherki", "Rayan Cherki"),
    (3, "N.Jackson", "Nicolas Jackson"),
    (4, "Le Fée", "Enzo Le Fée"),
    (5, "Thiago", "Igor Thiago Nascimento Rodrigues"),
    (6, "Williams", "Neco Williams"),
    (7, "Adams", "Tyler Adams"),
    (8, "Fernández", "Enzo Fernández"),
    (9, "Mosquera", "Cristhian Mosquera"),
    (10, "De Cuyper", "Maxim De Cuyper"),
    (11, "van Dijk", "Virgil van Dijk"),
    (12, "Iling-Junior", "Samuel Iling-Junior"),
    (13, "Savona", "Nicolò Savona"),
    (14, "Durán", "Carlos Alcaraz Durán"),
    # Two Palmers, so a bare surname stays ambiguous.
    (15, "C.Palmer", "Cole Palmer"),
    (16, "A.Palmer", "Alex Palmer"),
]


# ---------------------------------------------------------------------------
# Given-name variants: the same name, spelled differently on the two sides.


@pytest.mark.parametrize(
    "said, expected_id",
    [
        # Diminutive. The prefix comparison alone rejects this -- neither
        # "andy" nor "andrew" is a prefix of the other.
        ("Andy Robertson", 1),
        # Transliteration variant of one given name.
        ("Ryan Cherki", 2),
        ("Nicholas Jackson", 3),
        ("Igho Thiago", 5),
        ("Enso Le Fée", 4),
        # Applied to the pool side too: FPL's spelling is the arbitrary one as
        # often as the creator's.
        ("Andrew Robertson", 1),
        ("Rayan Cherki", 2),
    ],
)
def test_a_variant_spelling_of_one_given_name_resolves(said, expected_id):
    assert match_player_name(said, POOL) == expected_id


# ---------------------------------------------------------------------------
# Surname particles.


@pytest.mark.parametrize(
    "said, expected_id",
    [
        # `tokens` yields ('le', 'fee'), so before particles were recognised
        # 'le' was the only forename on offer -- two characters, which can
        # never satisfy _MIN_FORENAME_CHARS, so this was unmatchable.
        ("Le Fée", 4),
        ("Le Fee", 4),
        ("van Dijk", 11),
        ("De Cuyper", 10),
    ],
)
def test_a_particle_is_not_mistaken_for_a_forename(said, expected_id):
    assert match_player_name(said, POOL) == expected_id


def test_a_bare_particle_resolves_to_nobody():
    """A truncated name, not a surname. Matching on it would compare the
    wrong token entirely."""
    assert match_player_name("de", POOL) is None
    assert match_player_name("van", POOL) is None


# ---------------------------------------------------------------------------
# The half that matters: pairs the variant table must NOT collapse.


@pytest.mark.parametrize(
    "said",
    [
        # The documented case. Nico Williams (Athletic Bilbao) and Neco
        # Williams (Forest) are two people, and 'williams' is unique in this
        # pool -- so nothing but the forename disagreement prevents a false
        # attribution here.
        "Nico Williams",
        # Che Adams and Tyler Adams.
        "Che Adams",
        # Yerson Mosquera and Cristhian Mosquera.
        "Yerson Mosquera",
        # Vinícius Júnior is not in the Premier League; Samuel Iling-Junior
        # is. Phonetically these collide, which is exactly why a phonetic
        # repair pass was measured and rejected.
        "Vinícius Júnior",
        # Savinho and Nicolò Savona.
        "Savinho",
        # Jhon Durán and Carlos Alcaraz Durán.
        "Jhon Durán",
        # Bruno Fernandes is not in this pool; Enzo Fernández is.
        "Bruno Fernandez",
        # A manager, not a player.
        "Arne Slot",
    ],
)
def test_two_different_people_are_never_collapsed(said):
    assert match_player_name(said, POOL) is None


def test_an_ambiguous_surname_still_abstains():
    """Unchanged by any of this: `Palmer` reaches both Cole and Alex, so it
    resolves to neither."""
    assert match_player_name("Palmer", POOL) is None


def test_a_correct_full_name_still_resolves():
    """The variant table must not disturb the exact tier."""
    assert match_player_name("Enzo Fernández", POOL) == 8
    assert match_player_name("Cole Palmer", POOL) == 15
