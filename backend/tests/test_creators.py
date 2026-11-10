import os

import psycopg
import pytest
from psycopg.rows import dict_row

from fpl_coach.creators import add_creator, list_creators

TEST_HANDLES = ("test_creator_a", "test_creator_b")


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = ANY(%s)", (list(TEST_HANDLES),))


@pytest.fixture(autouse=True)
def _isolate():
    _cleanup()
    yield
    _cleanup()


def test_add_creator_with_a_trust_tier():
    with _conn() as conn:
        creator = add_creator(conn, "Test Creator A", "test_creator_a", "high")

    assert creator.name == "Test Creator A"
    assert creator.x_handle == "test_creator_a"
    assert creator.trust_tier == "high"
    assert creator.id is not None


def test_invalid_trust_tier_is_rejected():
    with _conn() as conn:
        with pytest.raises(ValueError, match="trust_tier"):
            add_creator(conn, "Bad Tier", "test_creator_a", "extreme")


def test_re_adding_the_same_handle_updates_it_rather_than_erroring():
    with _conn() as conn:
        first = add_creator(conn, "Test Creator A", "test_creator_a", "medium")
        second = add_creator(conn, "Test Creator A Renamed", "test_creator_a", "high")

    assert second.id == first.id
    assert second.trust_tier == "high"
    assert second.name == "Test Creator A Renamed"


def test_list_creators_returns_added_creators_sorted_by_name():
    with _conn() as conn:
        add_creator(conn, "Test Creator B", "test_creator_b", "low")
        add_creator(conn, "Test Creator A", "test_creator_a", "high")
        creators = list_creators(conn)

    names = [c.name for c in creators if c.x_handle in TEST_HANDLES]
    assert names == ["Test Creator A", "Test Creator B"]
