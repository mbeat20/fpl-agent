"""Issue 17: an evidence line that came from a creator says who said it, and
links to where -- to the exact second, for a video.

The load-bearing claim is that attribution is *looked up*, never authored by
the model: same discipline ADR-0004 applies to picks. So these tests care as
much about what the model CANNOT do (invent a url, cite an id it was never
shown) as about the happy path.

DB-backed tests here use their own creator handles and delete by handle, so
they do not touch the real roster on the shared Supabase project.
"""

import os
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from fpl_coach.ingestion.voyage_client import EMBEDDING_DIMENSION, vector_literal
from fpl_coach.models import (
    Citation,
    CreatorOpinions,
    CreatorStanceView,
    Evidence,
    Player,
    Recommendation,
    SemanticCreatorMatch,
    Team,
    TransferComparison,
    TransferOption,
    deep_link,
)
from fpl_coach.reasoning.base import ToolSpec
from fpl_coach.reasoning.claude import ClaudeReasoning
from fpl_coach.suggestions import get_cached_suggestion, save_suggestion
from fpl_coach.tools.get_creator_opinions import get_creator_opinions
from fpl_coach.tools.search_creator_opinions import search_creator_opinions
from conftest import fake_message_response

TEST_HANDLE = "cited_evidence_creator"

# ---------------------------------------------------------------------------
# deep_link -- the one place a url is assembled
# ---------------------------------------------------------------------------


def test_a_video_url_gets_the_timestamp_appended_to_its_existing_query():
    """A stored YouTube url is watch?v=<id>, which already has a query
    string, so a naive "?" would produce a url YouTube ignores."""
    link = deep_link("https://www.youtube.com/watch?v=abc123", 743)

    assert link == "https://www.youtube.com/watch?v=abc123&t=743s"


def test_a_url_with_no_query_gets_a_question_mark():
    assert deep_link("https://youtu.be/abc123", 743) == "https://youtu.be/abc123?t=743s"


def test_an_x_post_url_is_returned_untouched():
    """An X post has no timeline to seek into, so start_seconds is None and
    the citation is simply the post."""
    url = "https://x.com/someone/status/1234567890"

    assert deep_link(url, None) == url


def test_a_zero_second_start_is_not_a_timestamp():
    """?t=0s says nothing the bare url does not already say, and a 0 from a
    [00:00] marker is far more likely to be a parse artifact than a real
    claim made in the first second of a video."""
    assert deep_link("https://youtu.be/abc", 0) == "https://youtu.be/abc"
    assert deep_link("https://youtu.be/abc", -5) == "https://youtu.be/abc"


# ---------------------------------------------------------------------------
# Citation.from_stance
# ---------------------------------------------------------------------------


def test_a_stance_with_no_source_url_yields_no_citation():
    """A hand-built view (a test double, a fixture) has no content row behind
    it. That must produce no citation rather than a citation to nowhere."""
    view = CreatorStanceView(
        creator_name="Someone", trust_tier="high", stance="buy", confidence=0.8, reason="r"
    )

    assert Citation.from_stance(view) is None


def test_a_citation_carries_creator_trust_and_a_deep_link():
    view = SemanticCreatorMatch(
        creator_name="Let's Talk FPL",
        trust_tier="high",
        stance="captain",
        confidence=0.7,
        reason="Home fixture.",
        matched_text="[12:23] I'm going Salah captain",
        citation_id=99,
        source_url="https://www.youtube.com/watch?v=vid",
        start_seconds=743,
        published_at="2026-08-25T10:00:00+00:00",
    )

    citation = Citation.from_stance(view)

    assert citation.creator_name == "Let's Talk FPL"
    assert citation.trust_tier == "high"
    assert citation.url == "https://www.youtube.com/watch?v=vid&t=743s"
    assert citation.start_seconds == 743
    assert citation.published_at == "2026-08-25T10:00:00+00:00"


# ---------------------------------------------------------------------------
# Evidence shape
# ---------------------------------------------------------------------------


def test_an_uncited_evidence_line_is_valid():
    """AC4: a purely statistical line (xG, a price move) has no creator
    behind it and must not be forced to invent one."""
    item = Evidence(text="Haaland has 4.2 xG over the last five gameweeks.")

    assert item.citation is None


def test_evidence_must_carry_prose_even_when_it_carries_a_citation():
    with pytest.raises(ValidationError):
        Evidence(citation=Citation(creator_name="c", trust_tier="high", url="https://x"))


# ---------------------------------------------------------------------------
# The tools -- the reasoning layer cannot cite what the tool never returned
# ---------------------------------------------------------------------------


# Issue 18: the opinion tools resolve a planning gameweek to label staleness
# against, so they take a team id. Not MY_TEAM_ID -- these assertions are
# about citations, not about the real team.
TEST_TEAM_ID = 90218


def _conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def _unit_vector(axis: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIMENSION
    v[axis] = 1.0
    return v


class _FakeVoyageClient:
    def __init__(self, query_vector: list[float]) -> None:
        self._query_vector = query_vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._query_vector for _ in texts]


@pytest.fixture
def seeded_citable_content():
    """One creator with two content rows: a YouTube video whose Stance is
    timestamped, and an X post whose Stance is not. Both are embedded on
    near-orthogonal axes so search ordering is provable with no API calls.

    The video also gets a chunk with its OWN start_seconds different from
    the Stance's, which is what proves the COALESCE preference in
    search_creator_opinions.
    """
    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO creators (name, x_handle, trust_tier) "
                "VALUES (%s, %s, %s) RETURNING id",
                ("Cited Evidence Creator", TEST_HANDLE, "high"),
            )
            creator_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO creator_content "
                "(creator_id, source_url, content_text, platform, published_at, title) "
                "VALUES (%s, %s, %s, 'youtube', %s, %s) RETURNING id",
                (
                    creator_id,
                    "https://www.youtube.com/watch?v=citedvid",
                    "[12:23] Salah is the captain pick this week.",
                    "2026-08-25T10:00:00+00:00",
                    "GW3 Team Selection",
                ),
            )
            video_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO content_chunks "
                "(content_id, chunk_text, embedding, start_seconds) "
                "VALUES (%s, %s, %s::vector, %s)",
                (
                    video_id,
                    "[12:23] Salah is the captain pick this week.",
                    vector_literal(_unit_vector(0)),
                    700,
                ),
            )
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason, "
                " start_seconds) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    creator_id,
                    video_id,
                    "Mohamed Salah",
                    "captain",
                    0.9,
                    "Home fixture and penalties.",
                    743,
                ),
            )
            video_stance_id = cur.fetchone()["id"]

            cur.execute(
                "INSERT INTO creator_content "
                "(creator_id, source_url, content_text, platform) "
                "VALUES (%s, %s, %s, 'x') RETURNING id",
                (
                    creator_id,
                    f"https://x.com/{TEST_HANDLE}/status/1",
                    "Isak looks like a sell to me.",
                ),
            )
            post_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO content_chunks (content_id, chunk_text, embedding) "
                "VALUES (%s, %s, %s::vector)",
                (post_id, "Isak looks like a sell to me.", vector_literal(_unit_vector(1))),
            )
            cur.execute(
                "INSERT INTO creator_stances "
                "(creator_id, source_content_id, player_name, stance, confidence, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (creator_id, post_id, "Alexander Isak", "sell", 0.5, "Fixture run."),
            )
            post_stance_id = cur.fetchone()["id"]
        conn.commit()

    yield SimpleNamespace(
        creator_id=creator_id,
        video_stance_id=video_stance_id,
        post_stance_id=post_stance_id,
    )

    with _conn() as conn:
        conn.execute("DELETE FROM creators WHERE x_handle = %s", (TEST_HANDLE,))


def test_get_creator_opinions_returns_the_citation_fields(seeded_citable_content):
    """AC1. Without these the reasoning layer has nothing to attribute with,
    which is the actual work of this ticket."""
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Mohamed Salah", TEST_TEAM_ID)

    stance = next(s for s in opinions.stances if s.creator_name == "Cited Evidence Creator")
    assert stance.citation_id == seeded_citable_content.video_stance_id
    assert stance.source_url == "https://www.youtube.com/watch?v=citedvid"
    assert stance.start_seconds == 743
    assert stance.published_at is not None
    assert stance.published_at.startswith("2026-08-25")


def test_an_x_sourced_stance_is_citable_without_a_timestamp(seeded_citable_content):
    """An X post is still a source. It just has no second to seek to, and no
    published_at -- ticket 16 only records one for YouTube, and a fetched_at
    standing in for a publish date would be a quiet lie."""
    with _conn() as conn:
        opinions = get_creator_opinions(conn, "Alexander Isak", TEST_TEAM_ID)

    stance = next(s for s in opinions.stances if s.creator_name == "Cited Evidence Creator")
    assert stance.source_url.startswith("https://x.com/")
    assert stance.start_seconds is None
    assert stance.published_at is None
    assert Citation.from_stance(stance).url == stance.source_url


def test_search_creator_opinions_returns_the_citation_fields(seeded_citable_content):
    with _conn() as conn:
        result = search_creator_opinions(
            conn, _FakeVoyageClient(_unit_vector(0)), "who to captain", TEST_TEAM_ID
        )

    match = next(m for m in result.matches if m.creator_name == "Cited Evidence Creator")
    assert match.citation_id == seeded_citable_content.video_stance_id
    assert match.source_url == "https://www.youtube.com/watch?v=citedvid"
    assert match.published_at.startswith("2026-08-25")


def test_search_prefers_the_stances_own_timestamp_over_the_chunks(seeded_citable_content):
    """The chunk was seeded at 700s and the Stance at 743s. The Stance's is
    the one extraction pinned to the claim itself, so it wins; the chunk's is
    the fallback for a Stance extracted untimed."""
    with _conn() as conn:
        result = search_creator_opinions(
            conn, _FakeVoyageClient(_unit_vector(0)), "who to captain", TEST_TEAM_ID
        )

    match = next(m for m in result.matches if m.stance == "captain")
    assert match.start_seconds == 743


def test_search_falls_back_to_the_chunk_timestamp(seeded_citable_content):
    """A transcript Stance can be extracted with no [MM:SS] marker of its
    own. The matched chunk is what similarity actually found, so its start is
    still far better than dropping the user at minute zero."""
    with _conn() as conn:
        conn.execute(
            "UPDATE creator_stances SET start_seconds = NULL WHERE id = %s",
            (seeded_citable_content.video_stance_id,),
        )
        conn.commit()
        result = search_creator_opinions(
            conn, _FakeVoyageClient(_unit_vector(0)), "who to captain", TEST_TEAM_ID
        )

    match = next(m for m in result.matches if m.stance == "captain")
    assert match.start_seconds == 700
    assert Citation.from_stance(match).url.endswith("&t=700s")


# ---------------------------------------------------------------------------
# ClaudeReasoning -- attribution is looked up, not authored
# ---------------------------------------------------------------------------


def _tool_use(name, input_, id_):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=id_)


def _response(blocks):
    return fake_message_response(blocks)


def _team_tool():
    team = Team(
        id=1,
        name="Test Team",
        players=[
            Player(id=1, name="Mohamed Salah", position="MID", club="Liverpool", price=13.5),
            Player(id=2, name="Erling Haaland", position="FWD", club="Man City", price=15.0),
        ],
    )
    return ToolSpec(name="get_my_team", description="", input_schema={}, handler=lambda: team)


def _opinions_tool(stances):
    return ToolSpec(
        name="get_creator_opinions",
        description="",
        input_schema={},
        handler=lambda player_name: CreatorOpinions(player_name=player_name, stances=stances),
    )


_CITABLE_STANCE = CreatorStanceView(
    creator_name="Let's Talk FPL",
    trust_tier="high",
    stance="captain",
    confidence=0.9,
    reason="Home fixture and penalties.",
    citation_id=4242,
    source_url="https://www.youtube.com/watch?v=citedvid",
    start_seconds=743,
    published_at="2026-08-25T10:00:00+00:00",
)


def _wire(reasoning, responses):
    """Feeds canned model turns in order and records the kwargs of each call,
    so a test can assert on the tool schemas actually sent."""
    calls = []

    def fake_create(**kwargs):
        calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return responses[len(calls) - 1]

    reasoning._client.messages.create = fake_create
    return calls


def _submit(evidence, **overrides):
    args = {
        "decision": "captain",
        "pick_player_id": 1,
        "confidence": 0.8,
        "evidence": evidence,
        "risks": ["r"],
    }
    args.update(overrides)
    return _response([_tool_use("submit_recommendation", args, "submit_1")])


@pytest.fixture
def reasoning(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return ClaudeReasoning()


def test_a_cited_evidence_line_is_attributed_from_the_tool_result(reasoning):
    """AC2 + AC3. The model supplies only the id and the prose; creator,
    trust tier and the deep link all come from what the tool returned."""
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _submit([{"text": "Let's Talk FPL is on Salah for the armband.", "citation_id": 4242}]),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Who should I captain?", [_team_tool(), _opinions_tool([_CITABLE_STANCE])]
    )

    citation = result.evidence[0].citation
    assert result.evidence[0].text == "Let's Talk FPL is on Salah for the armband."
    assert citation.creator_name == "Let's Talk FPL"
    assert citation.trust_tier == "high"
    assert citation.url == "https://www.youtube.com/watch?v=citedvid&t=743s"
    assert citation.start_seconds == 743


def test_the_model_cannot_smuggle_in_its_own_url(reasoning):
    """The submit schema has no url field at all, so extra keys the model
    invents are ignored rather than trusted. A model-authored URL is a
    hallucinated URL."""
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _submit(
            [
                {
                    "text": "Someone said this.",
                    "citation_id": 4242,
                    "url": "https://evil.example/made-up",
                    "creator_name": "Not This Creator",
                }
            ]
        ),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Who should I captain?", [_team_tool(), _opinions_tool([_CITABLE_STANCE])]
    )

    assert result.evidence[0].citation.url == "https://www.youtube.com/watch?v=citedvid&t=743s"
    assert result.evidence[0].citation.creator_name == "Let's Talk FPL"


def test_an_invented_citation_id_is_dropped_and_the_prose_survives(reasoning):
    """A citation the conversation never saw is not a reason to fail the
    whole request -- but it is absolutely a reason not to show a source."""
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _submit([{"text": "Someone somewhere said this.", "citation_id": 999999}]),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Who should I captain?", [_team_tool(), _opinions_tool([_CITABLE_STANCE])]
    )

    assert result.evidence[0].text == "Someone somewhere said this."
    assert result.evidence[0].citation is None


def test_a_statistical_line_stays_uncited(reasoning):
    """AC4: no citation_id, no citation -- and no failure either."""
    _wire(reasoning, [_submit([{"text": "Haaland leads the squad on xG."}])])

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.evidence == [Evidence(text="Haaland leads the squad on xG.")]


def test_a_citation_id_invented_before_anything_is_citable_is_dropped(reasoning):
    """The schema used to omit citation_id entirely until a creator tool had
    returned something. That made the tool definition a function of
    conversation state, which is the one thing it must not be -- tools render
    at the front of the cached prefix, so it dropped the system prompt and the
    whole transcript out of cache mid-question.

    The property is now always offered and the guarantee is enforced where it
    is actually checkable: an id that resolves to nothing is dropped, and the
    line goes out honestly unattributed rather than wrongly attributed."""
    _wire(reasoning, [_submit([{"text": "x", "citation_id": 4242}])])

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.evidence[0].text == "x"
    assert result.evidence[0].citation is None


def test_only_a_stance_the_model_was_actually_shown_resolves_to_a_citation(reasoning):
    """The rule ADR-0014 cares about: the model chooses WHICH source backs a
    line, and the attribution is looked up from the tool result that produced
    it. An id it was shown resolves; one it invented does not."""
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _submit([{"text": "shown", "citation_id": 4242}, {"text": "invented", "citation_id": 9999}]),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Who should I captain?", [_team_tool(), _opinions_tool([_CITABLE_STANCE])]
    )

    shown, invented = result.evidence
    assert shown.citation is not None
    assert shown.citation.creator_name == "Let's Talk FPL"
    assert invented.citation is None


def test_a_stance_with_no_source_url_never_becomes_citable(reasoning):
    """Ticket 16 widened Stances to non-player subjects and hand-built views
    exist in fixtures; a view with a citation_id but no url must not produce
    an offer to cite it."""
    uncitable = CreatorStanceView(
        creator_name="No Source Creator",
        trust_tier="low",
        stance="buy",
        confidence=0.4,
        reason="r",
        citation_id=7,
    )
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _submit([{"text": "x", "citation_id": 7}]),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Who should I captain?", [_team_tool(), _opinions_tool([uncitable])]
    )

    # Never recorded as citable, so the id resolves to nothing even though the
    # model was free to write it.
    assert result.evidence[0].citation is None


def test_recommend_transfers_stances_are_citable_too(reasoning):
    """Issue 11's comparison embeds get_creator_opinions' stances in each
    side, so a transfer question gets citable creator opinion without the
    model having to make a second tool call for it."""
    salah = Player(id=1, name="Mohamed Salah", position="MID", club="Liverpool", price=13.5)
    haaland = Player(id=2, name="Erling Haaland", position="FWD", club="Man City", price=15.0)
    comparison = TransferComparison(
        player_out=TransferOption(player=haaland, creator_stances=[], injury_risk=False),
        player_in=TransferOption(
            player=salah, creator_stances=[_CITABLE_STANCE], injury_risk=False
        ),
        is_hit=False,
        hit_cost=0,
        gain_signal=1.0,
        net_gain_signal=1.0,
        data_complete=True,
    )
    transfers_tool = ToolSpec(
        name="recommend_transfers",
        description="",
        input_schema={},
        handler=lambda **_: comparison,
    )
    responses = [
        _response(
            [
                _tool_use(
                    "recommend_transfers",
                    {"player_out_name": "Haaland", "player_in_name": "Salah", "is_hit": False},
                    "call_1",
                )
            ]
        ),
        _submit(
            [{"text": "Let's Talk FPL rates the incoming player.", "citation_id": 4242}],
            decision="transfer",
        ),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason("Should I do Haaland to Salah?", [_team_tool(), transfers_tool])

    assert result.evidence[0].citation.creator_name == "Let's Talk FPL"


def test_plan_steps_carry_citations_the_same_way(reasoning):
    """AC5: a TransferPlan step's evidence is the same shape and gets the
    same lookup, not a second, looser path."""
    responses = [
        _response([_tool_use("get_creator_opinions", {"player_name": "Salah"}, "call_1")]),
        _response(
            [
                _tool_use(
                    "submit_plan",
                    {
                        "horizon": "multi-week",
                        "confidence": 0.6,
                        "steps": [
                            {
                                "gameweek": 3,
                                "decision": "captain",
                                "pick_player_id": 1,
                                "evidence": [
                                    {"text": "Let's Talk FPL is on Salah.", "citation_id": 4242}
                                ],
                                "risks": ["r"],
                            },
                            {
                                "gameweek": 4,
                                "decision": "roll",
                                "evidence": [{"text": "Bank the transfer."}],
                                "risks": [],
                            },
                        ],
                    },
                    "submit_1",
                )
            ]
        ),
    ]
    _wire(reasoning, responses)

    result = reasoning.reason(
        "Plan my next few weeks", [_team_tool(), _opinions_tool([_CITABLE_STANCE])]
    )

    assert result.steps[0].evidence[0].citation.url.endswith("&t=743s")
    assert result.steps[1].evidence[0].citation is None


def test_a_bare_string_evidence_item_degrades_to_an_uncited_line(reasoning):
    """Forgiving about an item, strict about the container. The schema asks
    for objects; a model slip should cost the citation, not the answer."""
    _wire(reasoning, [_submit(["just a plain string"])])

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.evidence == [Evidence(text="just a plain string")]


def test_a_non_list_evidence_value_is_still_bounced_back_to_the_model(reasoning):
    """Iterating a bare string would silently produce one evidence line per
    character, so the container type stays strict."""
    responses = [
        _submit("not a list"),
        _submit([{"text": "recovered"}]),
    ]
    calls = _wire(reasoning, responses)

    result = reasoning.reason("Who should I captain?", [_team_tool()])

    assert result.evidence == [Evidence(text="recovered")]
    error_result = calls[1]["messages"][-1]["content"][0]
    assert error_result["is_error"] is True


# ---------------------------------------------------------------------------
# The cached-suggestion migration path
# ---------------------------------------------------------------------------


def test_a_cached_row_under_the_old_evidence_shape_is_a_miss_not_a_crash():
    """AC7. team_suggestions is a cache, so the only sane failure mode for a
    row written under the previous shape is a miss the caller regenerates
    over. Asserted at the model boundary get_cached_suggestion catches on --
    no DB write needed to prove the ValidationError happens."""
    old_shape = {
        "decision": "captain",
        "pick": {"player_id": 1, "player_name": "Mohamed Salah"},
        "confidence": 0.7,
        "evidence": ["Salah has the best fixtures."],
        "risks": [],
        "alternative": None,
    }

    with pytest.raises(ValidationError):
        Recommendation(**old_shape)


def test_get_cached_suggestion_returns_none_for_an_old_shape_row(seeded_team):
    """The same thing again but through the real read path, because the AC is
    about the dashboard not 500ing -- a row is hand-written into the JSONB
    column in the previous shape, exactly as a pre-issue-17 run left it."""
    old_shape = {
        "decision": "captain",
        "pick": {"player_id": 1, "player_name": "Mohamed Salah"},
        "confidence": 0.7,
        "evidence": ["Salah has the best fixtures."],
        "risks": [],
        "alternative": None,
    }
    with _conn() as conn:
        conn.execute(
            "INSERT INTO team_suggestions (team_id, gameweek, recommendation) "
            "VALUES (%s, %s, %s) ON CONFLICT (team_id) DO UPDATE SET "
            "gameweek = EXCLUDED.gameweek, recommendation = EXCLUDED.recommendation",
            (seeded_team, 3, Jsonb(old_shape)),
        )
        conn.commit()

        assert get_cached_suggestion(conn, seeded_team, 3) is None

        # And a round trip in the new shape still hits, so the miss above is
        # the old shape's fault rather than a broken read.
        save_suggestion(
            conn,
            seeded_team,
            3,
            Recommendation(
                decision="roll",
                confidence=0.5,
                evidence=[
                    Evidence(
                        text="Let's Talk FPL is on Salah.",
                        citation=Citation(
                            creator_name="Let's Talk FPL",
                            trust_tier="high",
                            url="https://www.youtube.com/watch?v=citedvid&t=743s",
                            start_seconds=743,
                        ),
                    )
                ],
                risks=[],
            ),
        )
        cached = get_cached_suggestion(conn, seeded_team, 3)

    assert cached.evidence[0].citation.start_seconds == 743
