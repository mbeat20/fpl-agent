"""Issue 29: themes clustered from the embeddings ticket 09 already paid for.

The clustering itself is exercised with hand-built vectors so the geometry is
exact rather than hoped for: three creators placed close together in one
direction must become a theme, one creator repeating themselves must not.

`content_chunks.embedding` is `vector(1024)`, so the fixtures build unit-ish
vectors in that many dimensions -- a short vector would be rejected by the
column, and a random one would make the assertions luck.
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.api.routes import get_reasoning
from fpl_coach.app import app
from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION, vector_literal
from fpl_coach.themes import DEFAULT_THRESHOLD, MAX_PLAYERS, _label, build_themes

HANDLE_PREFIX = "themes_test_"
TEST_TEAM_ID = 90226


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _cleanup():
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle LIKE %s", (HANDLE_PREFIX + "%",))
        conn.execute("DELETE FROM teams WHERE id = %s", (TEST_TEAM_ID,))


def _vector(axis: int, tilt: float = 0.0) -> list[float]:
    """A unit vector pointing down one axis, optionally tilted towards the
    next. Two vectors on the same axis are at cosine distance ~0; two on
    different axes are at ~1, well outside any sane threshold."""
    values = [0.0] * EMBEDDING_DIMENSION
    values[axis] = 1.0
    if tilt:
        values[axis + 1] = tilt
    return values


@pytest.fixture
def seeded_themes():
    """Three creators saying the same thing (axis 0) -- a theme. One creator
    saying something else three times (axis 5) -- not a theme, however loud."""
    _cleanup()
    with _conn() as conn:
        conn.execute("INSERT INTO teams (id, name) VALUES (%s, %s)", (TEST_TEAM_ID, "Themes"))
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s)",
            (TEST_TEAM_ID, 40),
        )
        with conn.cursor() as cur:
            def creator(name):
                cur.execute(
                    "INSERT INTO creators (name, x_handle, trust_tier) "
                    "VALUES (%s, %s, 'high') RETURNING id",
                    (name, HANDLE_PREFIX + name),
                )
                return cur.fetchone()["id"]

            def content(creator_id, url, text, vector, players=()):
                cur.execute(
                    "INSERT INTO creator_content (creator_id, source_url, content_text, "
                    "published_at) VALUES (%s, %s, %s, now()) RETURNING id",
                    (creator_id, url, text),
                )
                content_id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                    "VALUES (%s, %s, %s)",
                    (content_id, text, vector_literal(vector)),
                )
                for player in players:
                    cur.execute(
                        "INSERT INTO creator_stances (creator_id, source_content_id, "
                        "player_name, stance, confidence, reason, gameweek) "
                        "VALUES (%s, %s, %s, 'buy', 0.8, 'because', 40)",
                        (creator_id, content_id, player),
                    )
                return content_id

            a, b, c, loud = (creator(n) for n in ("Ann", "Bo", "Cy", "Lou"))

            # The theme: three different creators, near-identical direction.
            content(a, "https://x.test/a1", "Arsenal are rotating their defence badly.",
                    _vector(0), players=("Timber", "Saka"))
            content(b, "https://x.test/b1", "The Arsenal rotation is being mispriced.",
                    _vector(0, 0.02), players=("Timber", "Gabriel"))
            content(c, "https://x.test/c1", "Nobody is pricing the Arsenal rotation.",
                    _vector(0, 0.04), players=("Timber",))

            # Not a theme: one creator, three posts, same direction.
            for n in range(3):
                content(loud, f"https://x.test/loud{n}", f"Buy Haaland, reason {n}.",
                        _vector(5, 0.01 * n), players=("Haaland",))
        conn.commit()
        yield conn

    _cleanup()


def _theme_about(board, word):
    return next((t for t in board.themes if word.lower() in t.label.lower()), None)


def test_three_creators_saying_the_same_thing_is_a_theme(seeded_themes):
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)

    theme = _theme_about(board, "Arsenal")
    assert theme is not None
    assert theme.creators == 3
    assert theme.creator_names == ["Ann", "Bo", "Cy"]


def test_one_creator_repeating_themselves_is_not_a_theme(seeded_themes):
    """Cross-creator density is the whole filter: volume from one account is
    that account being loud, not a theme."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)

    assert _theme_about(board, "Haaland") is None


def test_the_label_is_a_real_chunk_of_creator_text(seeded_themes):
    """ADR-0014 applied to labels. A generated theme name is the same
    hallucination surface as a generated citation -- plausible, unverifiable,
    and wrong in a way that looks authoritative."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)
    theme = _theme_about(board, "Arsenal")

    with seeded_themes.cursor() as cur:
        cur.execute("SELECT chunk_text FROM content_chunks")
        real_texts = {row["chunk_text"] for row in cur.fetchall()}

    assert theme.label in real_texts


def test_a_theme_carries_a_citation_to_its_medoid(seeded_themes):
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)
    theme = _theme_about(board, "Arsenal")

    assert theme.citation is not None
    assert theme.citation.creator_name in {"Ann", "Bo", "Cy"}
    assert theme.citation.url.startswith("https://x.test/")


def test_creators_and_mentions_are_reported_separately(seeded_themes):
    """One prolific account is creators=1, mentions=6. Collapsing them in
    either direction misleads -- ticket 19's rule."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)
    theme = _theme_about(board, "Arsenal")

    assert theme.creators == 3
    assert theme.mentions == 3


def test_a_theme_names_the_players_its_stances_name(seeded_themes):
    """Resolved through creator_content -> creator_stances, never by matching
    the label text against player names."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)
    theme = _theme_about(board, "Arsenal")

    # Timber is named by all three; the others by one each, so he leads.
    assert theme.players[0] == "Timber"
    assert set(theme.players) == {"Timber", "Saka", "Gabriel"}
    assert len(theme.players) <= MAX_PLAYERS


def test_unrelated_directions_do_not_cluster_together(seeded_themes):
    """The threshold has to actually separate things. Axis 0 and axis 5 are at
    cosine distance ~1, far outside it."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)
    theme = _theme_about(board, "Arsenal")

    assert "Haaland" not in theme.players


def test_a_raised_creator_floor_drops_the_theme(seeded_themes):
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=4)

    assert _theme_about(board, "Arsenal") is None


def test_nothing_to_cluster_is_reported_as_a_real_answer(seeded_themes):
    """"No group of creators is saying the same thing" is information, not a
    gap -- and must not render as noise."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=99)

    assert board.themes == []
    assert "real answer, not a gap" in board.note


def test_the_threshold_is_reported(seeded_themes):
    """The one tuning knob that changes what a theme IS, so it travels with
    the answer."""
    board = build_themes(seeded_themes, TEST_TEAM_ID, min_creators=3)

    assert board.threshold == DEFAULT_THRESHOLD
    assert board.chunks_considered >= 6


# ---------------------------------------------------------------------------
# Label trimming


def test_a_label_starting_mid_sentence_is_advanced_to_a_boundary():
    """A transcript is chunked on length, not grammar, so chunks routinely
    start mid-clause. The label should read as the quotation it is."""
    label = _label("in any specific week they are explosive. Then it is Wissa who leads.")

    assert label.startswith("Then it is Wissa")


def test_a_label_already_starting_cleanly_is_left_alone():
    assert _label("Arsenal are rotating badly.") == "Arsenal are rotating badly."


def test_a_long_label_is_trimmed_at_a_sentence_where_possible():
    text = "Arsenal are rotating badly. " + ("More words here. " * 40)

    label = _label(text)

    assert len(label) <= 240
    assert label.endswith(".")


# ---------------------------------------------------------------------------
# The route


class _RefusingReasoning:
    def reason(self, prompt, tools, system="", validate=None, history=None):
        raise AssertionError("themes must not make a reasoning call")


def test_themes_route_is_a_free_read(seeded_themes, monkeypatch):
    """No LLM anywhere -- including the labels. pgvector does the distance
    work and the label is a stored chunk."""
    monkeypatch.setattr("fpl_coach.api.routes.MY_TEAM_ID", TEST_TEAM_ID)
    app.dependency_overrides[get_reasoning] = _RefusingReasoning
    try:
        with TestClient(app) as client:
            response = client.get("/creators/themes")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["threshold"] == DEFAULT_THRESHOLD
    assert any("Arsenal" in t["label"] for t in body["themes"])
