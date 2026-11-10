"""Issue 19: creator consensus and the bandwagon meter.

The failure this guards against is the one the ticket names: a count that
cannot tell "eleven creators have liked him all season" from "two became ten
in four days". Both read as a big number; one is a template pick and the
other is a bandwagon forming.

Seeds its own creators, its own team and its own gameweek calendar, never
MY_TEAM_ID -- conftest's seeded_team fixture deletes the real team row and
cascade-wipes real state, and nothing here needs to go near it. The calendar
rows use gameweek ids far beyond the real season so they cannot collide with
what fpl-ingest writes.
"""

import itertools
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.consensus import classify_movement, summarise_window
from fpl_coach.gameweek import gameweek_at, load_deadlines, next_deadline
from fpl_coach.tools.get_creator_consensus import get_creator_consensus

TEST_TEAM_ID = 90219
# Well past a 38-gameweek season, so these calendar rows can never be
# confused with -- or overwritten by -- the real ones fpl-ingest writes.
PREV_GW = 60
CURRENT_GW = 61
CALENDAR_GWS = (59, PREV_GW, CURRENT_GW, 62)

# Enough creators to make "N of M" mean something, and named so cleanup can
# find them without touching the real 19.
HANDLES = tuple(f"consensus_{i}" for i in range(12))

NOW = datetime.now(timezone.utc)

# The test calendar sits entirely beyond the real season's last deadline.
# load_deadlines reads the whole gameweeks table, so once fpl-ingest has
# populated the real 38 rows they and these four share one sorted list -- and
# a test deadline interleaved among real ones would let a real gameweek win
# the "first deadline after this publish date" race and silently change what
# these tests measure.
_CALENDAR_BASE_DAYS = 400
_url_counter = itertools.count(1)


def _deadline_for(gameweek: int) -> datetime:
    return NOW + timedelta(days=_CALENDAR_BASE_DAYS + 7 * CALENDAR_GWS.index(gameweek))


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = ANY(%s)", (list(HANDLES),))
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))
        conn.execute("DELETE FROM gameweeks WHERE id = ANY(%s)", (list(CALENDAR_GWS),))


@pytest.fixture
def world():
    """A team pinned to CURRENT_GW, a four-gameweek calendar, and twelve
    creators. Yields a factory that files a Stance into a gameweek by giving
    it a publish date inside that gameweek's run-up -- never by setting the
    stored tag, which is what the ticket says not to trust."""
    _cleanup()
    with _conn() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Consensus"))
        # planning_gameweek takes max(persisted, upcoming-from-fixtures), so
        # pinning gameweek_state above every real gameweek is what makes
        # CURRENT_GW the planning gameweek regardless of where the live
        # season has got to.
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, CURRENT_GW),
        )
        for gameweek in CALENDAR_GWS:
            conn.execute(
                "INSERT INTO gameweeks (id, name, deadline_time) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET deadline_time = EXCLUDED.deadline_time",
                (gameweek, f"Gameweek {gameweek}", _deadline_for(gameweek)),
            )

        creator_ids = []
        with conn.cursor() as cur:
            for i, handle in enumerate(HANDLES):
                cur.execute(
                    "INSERT INTO creators (name, x_handle, trust_tier) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (f"Consensus Creator {i}", handle, "high"),
                )
                creator_ids.append(cur.fetchone()["id"])
        conn.commit()

        def stance(creator_index: int, player: str, kind: str, gameweek: int, stored=None):
            """One Stance from one creator, published inside `gameweek`'s
            run-up. `stored` sets the (unreliable) creator_stances.gameweek
            tag independently, so a test can prove the count ignores it."""
            published = _deadline_for(gameweek) - timedelta(days=2)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO creator_content "
                    "(creator_id, source_url, content_text, published_at) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (
                        creator_ids[creator_index],
                        f"https://x.com/c/status/{next(_url_counter)}",
                        f"{kind} {player}",
                        published,
                    ),
                )
                content_id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO creator_stances (creator_id, source_content_id, player_name, "
                    "stance, confidence, reason, gameweek) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        creator_ids[creator_index],
                        content_id,
                        player,
                        kind,
                        0.7,
                        f"because {kind}",
                        stored,
                    ),
                )
            conn.commit()

        yield conn, stance

    _cleanup()


# --- the counting unit: creators, not stances -------------------------------


def test_one_prolific_creator_is_counted_once(world):
    """Six posts from one account is one creator's opinion. Counting rows
    would render it as six, which is the exact illusion this tool exists to
    dispel -- and it was measured on the live database (8 of 8 returned
    stances for one player from a single creator)."""
    conn, stance = world
    for _ in range(6):
        stance(0, "Loudmouth", "buy", CURRENT_GW)

    result = get_creator_consensus(conn, "Loudmouth", TEST_TEAM_ID)

    assert result.current.creators_positive == 1
    assert result.current.creators_covering == 1
    assert result.current.stances == 6
    buys = next(item for item in result.current.by_stance if item.stance == "buy")
    assert (buys.creators, buys.stances) == (1, 6)


def test_a_creator_who_changed_their_mind_counts_once_as_their_latest_view(world):
    conn, stance = world
    stance(0, "Mindchanger", "hold", CURRENT_GW)
    stance(0, "Mindchanger", "buy", CURRENT_GW)
    # Both land two days before the deadline by default, so push the "hold"
    # a day earlier -- the point is that the LATER stance is the creator's
    # position, not whichever row was inserted last.
    conn.execute(
        "UPDATE creator_content SET published_at = published_at - interval '1 day' "
        "WHERE content_text = 'hold Mindchanger'"
    )
    conn.commit()

    result = get_creator_consensus(conn, "Mindchanger", TEST_TEAM_ID)

    assert result.current.creators_covering == 1
    assert result.current.creators_positive == 1
    assert result.current.creators_neutral == 0


# --- the derivative: the ticket's central requirement ------------------------


def test_a_settled_majority_and_a_fresh_surge_are_distinguishable(world):
    """The ticket's own words: "A player 11 of 12 creators have liked all
    season is a template pick; a player who went 2 -> 10 in four days is a
    bandwagon forming. These must not look the same."."""
    conn, stance = world
    for i in range(10):
        stance(i, "Template", "buy", PREV_GW)
        stance(i, "Template", "buy", CURRENT_GW)
    for i in range(2):
        stance(i, "Bandwagon", "buy", PREV_GW)
    for i in range(10):
        stance(i, "Bandwagon", "buy", CURRENT_GW)

    template = get_creator_consensus(conn, "Template", TEST_TEAM_ID)
    bandwagon = get_creator_consensus(conn, "Bandwagon", TEST_TEAM_ID)

    # Identical current level -- which is the whole trap.
    assert template.current.creators_positive == bandwagon.current.creators_positive == 10
    assert template.movement == "settled"
    assert bandwagon.movement == "surging"
    assert template.change.positive == 0
    assert bandwagon.change.positive == 8
    assert "sharp swing" in bandwagon.summary
    assert "steadily" in template.summary


def test_a_rise_from_no_coverage_is_not_reported_as_a_swing(world):
    """Nobody changed their mind -- the player entered the conversation.
    Calling that a bandwagon would cry wolf on every new differential and
    cost the tool its credibility for the cases that are real."""
    conn, stance = world
    for i in range(5):
        stance(i, "Newcomer", "buy", CURRENT_GW)

    result = get_creator_consensus(conn, "Newcomer", TEST_TEAM_ID)

    assert result.movement == "new_coverage"
    assert "rather than a measurable swing" in result.summary


def test_falling_positivity_reads_as_cooling(world):
    conn, stance = world
    for i in range(6):
        stance(i, "Fader", "buy", PREV_GW)
    for i in range(2):
        stance(i, "Fader", "buy", CURRENT_GW)

    result = get_creator_consensus(conn, "Fader", TEST_TEAM_ID)

    assert result.movement == "cooling"
    assert result.change.positive == -4


def test_silence_is_reported_as_absence_not_agreement(world):
    conn, _ = world
    result = get_creator_consensus(conn, "Nobodymentionshim", TEST_TEAM_ID)

    assert result.movement == "no_coverage"
    assert result.current.creators_covering == 0
    assert "not agreement" in result.summary


def test_negative_and_positive_stances_are_separated(world):
    conn, stance = world
    for i in range(4):
        stance(i, "Divisive", "buy", CURRENT_GW)
    for i in range(4, 7):
        stance(i, "Divisive", "sell", CURRENT_GW)
    stance(7, "Divisive", "hold", CURRENT_GW)

    result = get_creator_consensus(conn, "Divisive", TEST_TEAM_ID)

    assert result.current.creators_positive == 4
    assert result.current.creators_negative == 3
    assert result.current.creators_neutral == 1
    assert result.current.creators_covering == 8


# --- gameweek attribution: the ticket's known-bad-data requirement -----------


def test_gameweek_comes_from_publish_date_not_the_stored_tag(world):
    """AC: attribution comes from published_at against deadline times, not
    from creator_stances.gameweek -- which is a snapshot taken at the start of
    an ingestion run and had 591 live rows tagged with the wrong gameweek."""
    conn, stance = world
    # Every row deliberately carries a WRONG stored tag.
    for i in range(4):
        stance(i, "Mistagged", "buy", CURRENT_GW, stored=1)
    for i in range(2):
        stance(i, "Mistagged", "buy", PREV_GW, stored=1)

    result = get_creator_consensus(conn, "Mistagged", TEST_TEAM_ID)

    assert result.gameweek_source == "deadlines"
    assert result.current.gameweek == CURRENT_GW
    assert result.current.creators_positive == 4
    assert result.previous.gameweek == PREV_GW
    assert result.previous.creators_positive == 2


def test_undated_stances_are_counted_not_silently_dropped(world):
    conn, stance = world
    stance(0, "Halfdated", "buy", CURRENT_GW)
    stance(1, "Halfdated", "buy", CURRENT_GW)
    conn.execute(
        "UPDATE creator_content SET published_at = NULL WHERE content_text = 'buy Halfdated' "
        "AND id = (SELECT MAX(id) FROM creator_content WHERE content_text = 'buy Halfdated')"
    )
    conn.commit()

    result = get_creator_consensus(conn, "Halfdated", TEST_TEAM_ID)

    assert result.undated_stances == 1
    assert result.current.creators_positive == 1


def test_no_extra_api_calls_at_question_time(world, monkeypatch):
    """AC: counts are computed from stored Stances. A live HTTP call here
    would make opening the dashboard both slow and billable."""
    conn, stance = world
    stance(0, "Offline", "buy", CURRENT_GW)

    def explode(*args, **kwargs):
        raise AssertionError("get_creator_consensus made an HTTP request")

    monkeypatch.setattr("httpx.Client.request", explode)
    monkeypatch.setattr("httpx.Client.send", explode)

    result = get_creator_consensus(conn, "Offline", TEST_TEAM_ID)
    assert result.current.creators_positive == 1


# --- the Instinct join (ticket 13's intent) ---------------------------------


def test_a_live_instinct_naming_the_player_is_surfaced_with_the_change(world):
    conn, stance = world
    for i in range(2):
        stance(i, "Enzo", "buy", PREV_GW)
    for i in range(9):
        stance(i, "Enzo", "buy", CURRENT_GW)
    conn.execute(
        "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
        (TEST_TEAM_ID, CURRENT_GW, "thinking Rogers to Enzo"),
    )
    conn.commit()

    result = get_creator_consensus(conn, "Enzo", TEST_TEAM_ID)

    assert [note.text for note in result.instincts] == ["thinking Rogers to Enzo"]
    assert result.instinct_note is not None
    assert "2 -> 9" in result.instinct_note
    # The app makes the influence visible and then stops -- preferences.md is
    # explicit that the call stays the user's.
    assert "ask whether" in result.instinct_note
    assert "not silently override" in result.instinct_note.lower()


def test_no_instinct_means_no_instinct_note(world):
    conn, stance = world
    stance(0, "Unnoted", "buy", CURRENT_GW)

    result = get_creator_consensus(conn, "Unnoted", TEST_TEAM_ID)

    assert result.instincts == []
    assert result.instinct_note is None


def test_an_instinct_from_a_past_gameweek_does_not_surface(world):
    """Ticket 13: an Instinct is gameweek-scoped and expires at its deadline.
    Surfacing last week's note against this week's consensus would compare
    two different decisions."""
    conn, stance = world
    stance(0, "Expired", "buy", CURRENT_GW)
    conn.execute(
        "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
        (TEST_TEAM_ID, PREV_GW, "thinking about Expired"),
    )
    conn.commit()

    result = get_creator_consensus(conn, "Expired", TEST_TEAM_ID)

    assert result.instincts == []


# --- consensus never becomes a pick or a citation ---------------------------


def test_consensus_carries_no_citation_ids(world):
    """ADR-0014: a citation is looked up from a Stance the model was shown. A
    count is an aggregate and has no single source, so an evidence line
    resting on it is correctly uncited -- and there is no id here for the
    model to attach to one."""
    conn, stance = world
    stance(0, "Uncitable", "buy", CURRENT_GW)

    result = get_creator_consensus(conn, "Uncitable", TEST_TEAM_ID)
    dumped = result.model_dump()

    assert "citation_id" not in repr(dumped)
    assert "source_url" not in repr(dumped)


# --- the pure classification logic, without a database ----------------------


def _window(covering, positive, gameweek=1):
    return summarise_window(
        gameweek,
        [
            {
                "stance_id": i,
                "creator_name": f"c{i}",
                "stance": "buy" if i < positive else "hold",
                "published_at": NOW,
            }
            for i in range(covering)
        ],
    )


@pytest.mark.parametrize(
    "current, previous, expected",
    [
        ((0, 0), (0, 0), "no_coverage"),
        ((5, 5), (0, 0), "new_coverage"),
        ((0, 0), (5, 5), "dropped_off"),
        ((10, 10), (2, 2), "surging"),
        ((3, 3), (1, 1), "surging"),
        ((2, 2), (6, 6), "cooling"),
        ((1, 1), (3, 3), "cooling"),
        ((11, 11), (11, 11), "settled"),
        ((4, 4), (5, 5), "settled"),
        ((2, 2), (2, 2), "steady"),
    ],
)
def test_movement_classification(current, previous, expected):
    assert (
        classify_movement(_window(*current), _window(*previous, gameweek=0)) == expected
    )


# --- the calendar itself ----------------------------------------------------


def test_gameweek_at_places_content_before_the_deadline_it_precedes():
    deadlines = [(1, NOW), (2, NOW + timedelta(days=7)), (3, NOW + timedelta(days=14))]

    assert gameweek_at(deadlines, NOW - timedelta(hours=1)) == 1
    assert gameweek_at(deadlines, NOW + timedelta(hours=1)) == 2
    assert gameweek_at(deadlines, NOW + timedelta(days=8)) == 3
    # Past the last known deadline there is no gameweek left to be talking
    # about, and inventing one would file real opinion under a gameweek that
    # does not exist.
    assert gameweek_at(deadlines, NOW + timedelta(days=30)) is None
    assert gameweek_at(deadlines, None) is None


def test_next_deadline_skips_the_ones_already_passed():
    deadlines = [(1, NOW - timedelta(days=7)), (2, NOW + timedelta(days=1))]

    assert next_deadline(deadlines, NOW) == (2, NOW + timedelta(days=1))
    assert next_deadline(deadlines, NOW + timedelta(days=2)) is None


def test_load_deadlines_reports_where_the_calendar_came_from(world):
    conn, _ = world
    deadlines, source = load_deadlines(conn)

    assert source in ("deadlines", "first_kickoff")
    assert deadlines
    assert deadlines == sorted(deadlines, key=lambda row: row[1])
