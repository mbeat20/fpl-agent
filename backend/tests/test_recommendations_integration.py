"""Seam-2: exercises the real Claude-backed Reasoning implementation.

Excluded from the default run (see [tool.pytest.ini_options] addopts in
pyproject.toml). Requires ANTHROPIC_API_KEY. Run explicitly with:

    uv run pytest -m integration
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fpl_coach.app import app
from fpl_coach.ingestion.voyage_client import VoyageClient, vector_literal
from fpl_coach.models import Recommendation, TransferPlan
from fpl_coach.preferences import build_system_prompt, load_preferences
from fpl_coach.reasoning import ClaudeReasoning
from fpl_coach.tools import (
    make_compare_players_tool,
    make_get_injury_news_tool,
    make_get_instincts_tool,
    make_get_my_team_tool,
    make_get_player_tool,
    make_predict_price_changes_tool,
    make_recommend_transfers_tool,
    make_search_creator_opinions_tool,
)

pytestmark = pytest.mark.integration

NON_SQUAD_PLAYER_ID = 921


def test_claude_reasoning_returns_a_valid_recommendation(seeded_team):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tool = make_get_my_team_tool(conn, seeded_team)
        recommendation = ClaudeReasoning().reason("Who should I captain this week?", [tool])

    seeded_names = {"Mohamed Salah", "Erling Haaland", "Bukayo Saka"}
    assert recommendation.pick.player_name in seeded_names
    assert recommendation.alternative.player_name in seeded_names
    assert recommendation.alternative.player_id != recommendation.pick.player_id
    assert 0.0 <= recommendation.confidence <= 1.0
    assert recommendation.evidence
    assert recommendation.risks


def test_preference_changes_how_the_real_claude_recommendation_is_framed(
    seeded_team, tmp_path, monkeypatch
):
    """ADR-0010: Preferences colour every subsequent Recommendation."""
    monkeypatch.setenv("PREFERENCES_PATH", str(tmp_path / "preferences.md"))
    (tmp_path / "preferences.md").write_text(
        "- You must begin every single risk you list with the exact literal "
        "tag 'PREFTEST:' (all caps, followed by a colon and a space).\n"
    )

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tool = make_get_my_team_tool(conn, seeded_team)
        system = build_system_prompt(load_preferences())
        recommendation = ClaudeReasoning().reason(
            "Who should I captain this week?", [tool], system=system
        )

    assert recommendation.risks
    assert all(risk.startswith("PREFTEST:") for risk in recommendation.risks)


@pytest.fixture
def non_squad_player():
    """A pool player (issue 05) with Understat stats, not part of the seeded
    squad — real target for "is this player worth buying?"."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = %s", (NON_SQUAD_PLAYER_ID,))
        conn.execute(
            "INSERT INTO fpl_players "
            "(id, name, full_name, position, club, price, ownership, form, status, news, "
            " xg, xa) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                NON_SQUAD_PLAYER_ID,
                "Watkins",
                "Ollie Watkins",
                "FWD",
                "Aston Villa",
                9.0,
                12.5,
                6.8,
                "a",
                None,
                9.4,
                4.1,
            ),
        )
    yield NON_SQUAD_PLAYER_ID
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = %s", (NON_SQUAD_PLAYER_ID,))


def test_claude_looks_up_and_cites_stats_for_a_player_outside_the_squad(
    seeded_team, non_squad_player
):
    """Issue 05's actual acceptance criterion: asking about a player not in
    the squad must make Claude call get_player/compare_players (widening
    submit_recommendation's id enum past the squad-only starting set via
    _record_known_players) and cite the returned xG/xA as evidence, rather
    than falling back to a squad pick."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_get_player_tool(conn),
            make_compare_players_tool(conn),
        ]
        recommendation = ClaudeReasoning().reason(
            "Is Ollie Watkins worth buying? Cite his underlying stats.", tools
        )

    assert recommendation.pick.player_id == non_squad_player
    assert recommendation.pick.player_name == "Watkins"
    assert any(
        "xg" in item.lower() or "xa" in item.lower() or "9.4" in item or "4.1" in item
        for item in recommendation.evidence
    )


@pytest.fixture
def squad_with_an_injury(seeded_team):
    """Marks one seeded squad player as doubtful — issue 07's target signal
    for "what are the risks this week?" to surface."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "UPDATE players SET status = %s, news = %s "
            "WHERE team_id = %s AND name = 'Erling Haaland'",
            ("d", "Knock - 75% chance of playing", seeded_team),
        )
        conn.commit()
    yield seeded_team


def test_claude_summarizes_risk_across_the_whole_squad_not_just_one_player(
    squad_with_an_injury,
):
    """Issue 07's actual acceptance criterion: "what are the risks this
    week?" must produce a Recommendation whose risks cover the whole squad
    (multiple players), not only the one doubtful player."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, squad_with_an_injury),
            make_get_injury_news_tool(conn, squad_with_an_injury),
        ]
        recommendation = ClaudeReasoning().reason(
            "What are the risks this week? Summarize risk for my whole squad, "
            "not just one player.",
            tools,
        )

    seeded_last_names = {"Salah", "Haaland", "Saka"}
    mentioned = {
        name for risk in recommendation.risks for name in seeded_last_names if name in risk
    }
    assert len(mentioned) >= 2, recommendation.risks
    assert any("Haaland" in risk for risk in recommendation.risks)


def test_endpoint_repeated_calls_return_the_identical_pick_with_real_claude(seeded_team):
    with TestClient(app) as client:
        first = client.post("/recommendations", json={"question": "Who should I captain?"})
        second = client.post("/recommendations", json={"question": "Who should I captain?"})

    assert first.status_code == second.status_code == 200
    assert first.json()["pick"] == second.json()["pick"]


PRICE_RISER_IDS = (981, 982)


@pytest.fixture
def pool_with_a_clear_price_riser():
    """Issue 08's target signal — a low-ownership pool player with a strong
    positive net-transfer count, distinct enough from a flat/no-movement
    player that a real Claude call should flag it as a riser and not the
    other one."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(PRICE_RISER_IDS),))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fpl_players "
                "(id, name, full_name, position, club, price, ownership, "
                " transfers_in_event, transfers_out_event, cost_change_event) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (981, "Ramsdale", "Aaron Ramsdale", "GK", "Southampton", 4.5, 3.0, 15000, 200, 0),
                    (982, "Flat", "No Movement Player", "MID", "Wolves", 5.5, 8.0, 300, 300, 0),
                ],
            )
        conn.commit()
    yield PRICE_RISER_IDS
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(PRICE_RISER_IDS),))


def test_claude_flags_the_price_riser_with_evidence(seeded_team, pool_with_a_clear_price_riser):
    """Issue 08's actual acceptance criterion: "which players are expected
    to rise?" must make Claude call predict_price_changes and surface the
    flagged player with the net-transfer evidence behind the call, not just
    pick an arbitrary squad player. Exercises _record_known_players widening
    submit_recommendation's id enum for PriceChangePredictions results."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_predict_price_changes_tool(conn),
        ]
        recommendation = ClaudeReasoning().reason(
            "Which players are expected to rise in price? Give me the evidence.", tools
        )

    assert recommendation.pick.player_id == 981
    assert any("transfer" in item.lower() for item in recommendation.evidence)


@pytest.fixture
def instinct_this_gameweek(seeded_team):
    """Issue 13: an early-week Instinct tagged to the seeded team's current
    gameweek. The note asks Claude to quote it verbatim with a distinctive
    tag when relevant -- same trick as the PREFTEST preference test above,
    used here for deterministic assertions on otherwise-free-form prose."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (seeded_team, 12),
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instincts (team_id, gameweek, text) VALUES (%s, %s, %s)",
                (
                    seeded_team,
                    12,
                    "thinking Haaland over Salah for captain -- if this note is relevant to "
                    "the question, quote it verbatim in one of your risks, prefixed with the "
                    "exact literal tag 'INSTINCTTEST:' (all caps, followed by a colon and a "
                    "space).",
                ),
            )
        conn.commit()
    yield seeded_team
    # No explicit cleanup: seeded_team's own teardown deletes the team row,
    # which cascades gameweek_state and instincts (schema.sql FKs).


def test_claude_surfaces_a_relevant_instinct_and_prompts_reconciliation(
    seeded_team, instinct_this_gameweek
):
    """Issue 13's actual acceptance criteria: a later captaincy question in
    the same gameweek must surface the matching Instinct alongside the
    Recommendation, and prompt the user to say whether their reasoning
    changed rather than silently overriding it."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_get_instincts_tool(conn, seeded_team),
        ]
        recommendation = ClaudeReasoning().reason("Who should I captain this week?", tools)

    combined = " ".join(recommendation.evidence + recommendation.risks)
    assert "INSTINCTTEST:" in combined
    assert "Haaland" in combined
    assert "Salah" in combined


SEARCH_TEST_HANDLE = "search_int_test_creator"


@pytest.fixture
def creator_with_embedded_stance():
    """Issue 09: a real creator_content row, embedded with a real Voyage
    call (not a fake vector) so the tool's own live query-time embedding
    lands in the same, meaningful embedding space -- a fake axis-aligned
    vector (test_search_creator_opinions.py's unit tests) would make
    real-vs-fake cosine similarity meaningless here. The Stance's reason
    carries a distinctive tag Claude is asked to quote verbatim, same
    determinism trick as INSTINCTTEST/PREFTEST above."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (SEARCH_TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) VALUES (%s, %s, %s) "
                "RETURNING id",
                ("Search Integration Test Creator", SEARCH_TEST_HANDLE, "high"),
            )
            creator_id = cur.fetchone()["id"]

            text = "Newcastle's fixture run over the next five gameweeks is brutal for their defenders."
            cur.execute(
                "INSERT INTO creator_content (creator_id, source_url, content_text) "
                "VALUES (%s, %s, %s) RETURNING id",
                (creator_id, f"https://x.com/{SEARCH_TEST_HANDLE}/status/1", text),
            )
            content_id = cur.fetchone()["id"]

            embedding = VoyageClient().embed([text])[0]
            cur.execute(
                "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                "VALUES (%s, %s, %s::vector)",
                (content_id, text, vector_literal(embedding)),
            )
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    creator_id,
                    content_id,
                    "Sven Botman",
                    "sell",
                    0.65,
                    "Newcastle's brutal fixture run -- if you use this, quote it verbatim in "
                    "your evidence or risks, prefixed with the exact literal tag "
                    "'SEARCHTEST:' (all caps, followed by a colon and a space).",
                ),
            )
        conn.commit()
    yield creator_id
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM creators WHERE id = %s", (creator_id,))  # cascades content/chunks/stances


def test_claude_uses_semantic_search_for_a_query_with_no_exact_player_name(
    seeded_team, creator_with_embedded_stance
):
    """Ticket 09's actual acceptance criteria: a loose query about a team's
    fixture run (no exact player name) reaches search_creator_opinions and
    resolves back to the underlying Creator Stance's structured fields."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_search_creator_opinions_tool(conn, VoyageClient()),
        ]
        recommendation = ClaudeReasoning().reason(
            "What are creators saying about Newcastle's fixture run?", tools
        )

    combined = " ".join(recommendation.evidence + recommendation.risks)
    assert "SEARCHTEST:" in combined


TRANSFER_CANDIDATE_IDS = (971, 972)


@pytest.fixture
def transfer_candidates():
    """Issue 11: a clearly weak player and a clearly strong one, distinct
    enough that recommend_transfers's gain_signal should point unambiguously
    one way -- same "obvious winner" shape as issue 08's price-riser fixture
    above, so real Claude's pick is reproducible rather than a coin flip."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TRANSFER_CANDIDATE_IDS),))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fpl_players "
                "(id, name, full_name, position, club, price, ownership, form, status, "
                " news, xg, xa) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        971, "Weak Player", "Transfer Weak Candidate", "MID", "Test Town",
                        5.0, 5.0, 1.0, "a", None, 0.5, 0.2,
                    ),
                    (
                        972, "Strong Player", "Transfer Strong Candidate", "FWD", "Test City",
                        9.0, 15.0, 6.0, "a", None, 8.0, 4.0,
                    ),
                ],
            )
        conn.commit()
    yield TRANSFER_CANDIDATE_IDS
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("DELETE FROM fpl_players WHERE id = ANY(%s)", (list(TRANSFER_CANDIDATE_IDS),))


def test_recommend_transfers_is_the_only_way_to_reach_a_player_it_surfaces(
    seeded_team, transfer_candidates
):
    """Exercises _record_known_players widening submit_recommendation's id
    enum for TransferComparison results -- deliberately excludes get_player/
    compare_players from the tools list so Strong Player (972) is reachable
    ONLY through recommend_transfers. Without the _record_known_players
    branch, Claude could never submit 972 as a pick (issue 08's known-
    players gap, memory-documented)."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_recommend_transfers_tool(conn),
        ]
        recommendation = ClaudeReasoning().reason(
            "Should I transfer Weak Player for Strong Player? Assume it's a free transfer.",
            tools,
        )

    assert 972 in (recommendation.pick.player_id, recommendation.alternative.player_id)


def test_claude_pushes_back_on_a_transfer_the_data_does_not_support(
    seeded_team, transfer_candidates
):
    """Issue 11's actual acceptance criterion: given a transfer the evidence
    doesn't support (a hit, trading the strong player for the weak one),
    Claude must push back citing that evidence rather than agreeing."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_recommend_transfers_tool(conn),
        ]
        recommendation = ClaudeReasoning().reason(
            "I'm thinking of transferring Strong Player for Weak Player, and it would cost "
            "me a hit. Talk me out of it if the data doesn't support it.",
            tools,
        )

    combined = " ".join(recommendation.evidence + recommendation.risks)
    assert "PUSHBACK:" in combined
    assert recommendation.pick.player_id == 972  # keep Strong Player -- don't make the move


def test_endpoint_repeated_calls_return_the_identical_transfer_pick(
    seeded_team, transfer_candidates
):
    """ADR-0004: the primary pick for a transfer question must be
    deterministic across repeated calls, same as the existing captain-pick
    determinism test above."""
    with TestClient(app) as client:
        question = {
            "question": "Should I transfer Weak Player for Strong Player? Assume it's a "
            "free transfer."
        }
        first = client.post("/recommendations", json=question)
        second = client.post("/recommendations", json=question)

    assert first.status_code == second.status_code == 200
    assert first.json()["pick"] == second.json()["pick"]


def _seed_gameweek(team_id: int, gameweek: int) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "INSERT INTO gameweek_state (team_id, gameweek) VALUES (%s, %s) "
            "ON CONFLICT (team_id) DO UPDATE SET gameweek = EXCLUDED.gameweek",
            (team_id, gameweek),
        )
        conn.commit()


def test_claude_produces_a_multi_week_plan_for_a_long_term_question(
    seeded_team, transfer_candidates
):
    """Issue 12's actual acceptance criteria: a long-term question resolves
    to a multi-gameweek TransferPlan (not a single-gameweek Recommendation),
    with each step grounded in the same kind of evidence a single-gameweek
    Recommendation would cite (here, via recommend_transfers)."""
    _seed_gameweek(seeded_team, 8)
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_recommend_transfers_tool(conn),
        ]
        result = ClaudeReasoning().reason(
            "What should my long-term transfer plan be? I want a plan spanning "
            "several gameweeks, not just this week's pick. Consider transferring "
            "Weak Player out for Strong Player at some point in that plan.",
            tools,
        )

    assert isinstance(result, TransferPlan)
    assert len(result.steps) >= 2
    gameweeks = [step.gameweek for step in result.steps]
    # Not "each step its own gameweek": two moves in one gameweek (a free
    # transfer plus a hit) is legitimate FPL, and neither the submit_plan
    # schema nor issue 12's ACs forbid it. What actually matters is that the
    # plan is ordered and genuinely spans a horizon rather than sitting on
    # one gameweek.
    assert gameweeks == sorted(gameweeks)
    assert gameweeks[0] >= 8  # anchored to the seeded current gameweek
    assert gameweeks[-1] > gameweeks[0]  # genuinely spans a horizon
    assert all(step.evidence for step in result.steps)


def test_single_gameweek_question_still_resolves_to_a_recommendation(
    seeded_team, transfer_candidates
):
    """Issue 12's 4th acceptance criterion: a single-gameweek question still
    resolves to a Recommendation, confirming the two Horizons don't
    interfere with each other. Both questions are transfer questions naming
    the same two candidates -- deliberately, since this tool set (get_my_team
    + recommend_transfers, no get_player/compare_players) has nothing suited
    to ranking squad players against each other for captaincy, and asking a
    captaincy question against it makes Claude misuse recommend_transfers to
    try, chewing through the turn budget on a tool mismatch that has nothing
    to do with the Horizon this test actually exercises."""
    _seed_gameweek(seeded_team, 8)
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        tools = [
            make_get_my_team_tool(conn, seeded_team),
            make_recommend_transfers_tool(conn),
        ]
        plan = ClaudeReasoning().reason(
            "What should my long-term transfer plan be? I want a plan spanning "
            "several gameweeks, not just this week's pick. Consider transferring "
            "Weak Player out for Strong Player at some point in that plan.",
            tools,
        )
        single = ClaudeReasoning().reason(
            "Should I transfer Weak Player for Strong Player this gameweek? Assume "
            "it's a free transfer.",
            tools,
        )

    assert isinstance(plan, TransferPlan)
    assert isinstance(single, Recommendation)
