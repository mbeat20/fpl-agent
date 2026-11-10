"""Issue 16: a Creator is followed on more than one platform."""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.creators import add_creator, add_creator_source, list_creator_sources

NAMES = ("Source Test A", "Source Test B")
CHANNEL_A = "UCsourcetestAsourcetestA"
CHANNEL_B = "UCsourcetestBsourcetestB"


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@pytest.fixture(autouse=True)
def _isolate():
    def cleanup():
        with _conn() as conn:
            conn.execute("DELETE FROM creators WHERE name = ANY(%s)", (list(NAMES),))

    cleanup()
    yield
    cleanup()


def test_a_creator_can_be_added_with_no_x_handle():
    """The ticket-06 shape made this impossible (x_handle NOT NULL), so a
    YouTube-only creator could only be added by inventing an X handle for
    them -- a wrong identity in the trust record."""
    with _conn() as conn:
        creator = add_creator(conn, NAMES[0], None, "medium")

    assert creator.x_handle is None
    assert creator.id is not None


def test_re_adding_a_handle_less_creator_updates_it_rather_than_duplicating():
    """ON CONFLICT (x_handle) never fires for NULL, so the upsert has to key
    on name instead -- otherwise every re-run inserts a duplicate."""
    with _conn() as conn:
        first = add_creator(conn, NAMES[0], None, "low")
        second = add_creator(conn, NAMES[0], None, "high")

    assert second.id == first.id
    assert second.trust_tier == "high"


def test_giving_a_handle_less_creator_an_x_handle_later_keeps_the_same_row():
    with _conn() as conn:
        first = add_creator(conn, NAMES[0], None, "medium")
        with_handle = add_creator(conn, NAMES[0], "source_test_a", "medium")

    assert with_handle.id == first.id
    assert with_handle.x_handle == "source_test_a"


def test_an_x_creators_upsert_still_keys_on_the_handle():
    """Ticket 06's behaviour must be unchanged: renaming a creator whose
    handle is unchanged updates the existing row."""
    with _conn() as conn:
        first = add_creator(conn, NAMES[0], "source_test_a", "medium")
        renamed = add_creator(conn, NAMES[1], "source_test_a", "high")

    assert renamed.id == first.id
    assert renamed.name == NAMES[1]


def test_a_name_and_handle_held_by_two_different_creators_is_rejected():
    """Silently picking one would merge two identities in the trust record,
    and ADR-0001's whole premise is that trust is per-creator."""
    with _conn() as conn:
        add_creator(conn, NAMES[0], "source_test_a", "medium")
        add_creator(conn, NAMES[1], "source_test_b", "medium")
        with pytest.raises(ValueError, match="more than one existing creator"):
            add_creator(conn, NAMES[0], "source_test_b", "medium")


def test_a_youtube_source_stores_the_channel_id_not_the_handle():
    """Handles get renamed; channel ids do not. Storing the id is what makes
    resolution a once-at-add-time cost rather than a per-run one."""
    with _conn() as conn:
        creator = add_creator(conn, NAMES[0], None, "medium")
        source = add_creator_source(conn, creator.id, "youtube", CHANNEL_A, "SomeHandle")

    assert source.external_id == CHANNEL_A
    assert source.handle == "SomeHandle"
    assert source.platform == "youtube"


def test_an_unknown_platform_is_rejected():
    with _conn() as conn:
        creator = add_creator(conn, NAMES[0], None, "medium")
        with pytest.raises(ValueError, match="platform"):
            add_creator_source(conn, creator.id, "tiktok", "whatever")


def test_re_adding_the_same_channel_moves_it_rather_than_erroring():
    """UNIQUE (platform, external_id) stops one channel being counted under
    two creators, which would double-count that creator in ticket 19's
    consensus. Re-adding must fix the attribution, not raise."""
    with _conn() as conn:
        first = add_creator(conn, NAMES[0], None, "medium")
        second = add_creator(conn, NAMES[1], None, "medium")
        add_creator_source(conn, first.id, "youtube", CHANNEL_A)
        moved = add_creator_source(conn, second.id, "youtube", CHANNEL_A)

        assert moved.creator_id == second.id
        assert [s.creator_id for s in list_creator_sources(conn, creator_ids=[first.id])] == []


def test_sources_can_be_listed_by_platform_and_by_creator():
    with _conn() as conn:
        a = add_creator(conn, NAMES[0], "source_test_a", "medium")
        b = add_creator(conn, NAMES[1], None, "medium")
        add_creator_source(conn, a.id, "youtube", CHANNEL_A)
        add_creator_source(conn, a.id, "x", "x-id-source-test-a")
        add_creator_source(conn, b.id, "youtube", CHANNEL_B)

        youtube = list_creator_sources(conn, platform="youtube", creator_ids=[a.id, b.id])
        for_a = list_creator_sources(conn, creator_ids=[a.id])

    assert {s.external_id for s in youtube} == {CHANNEL_A, CHANNEL_B}
    assert {s.platform for s in for_a} == {"x", "youtube"}


def test_deleting_a_creator_cascades_to_its_sources():
    with _conn() as conn:
        creator = add_creator(conn, NAMES[0], None, "medium")
        add_creator_source(conn, creator.id, "youtube", CHANNEL_A)
        conn.execute("DELETE FROM creators WHERE id = %s", (creator.id,))
        conn.commit()

        assert list_creator_sources(conn, creator_ids=[creator.id]) == []
