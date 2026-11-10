import json
from pathlib import Path

import httpx

from fpl_coach.ingestion.grok_client import GrokClient

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "xai_x_search_response.json").read_text())


def _fake_client(payload: dict) -> GrokClient:
    """Fakes xAI's API at its HTTP boundary (httpx.MockTransport with canned
    JSON captured from a real call, 2026-08-18), same convention as
    FPLClient."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        base_url="https://api.x.ai",
        transport=transport,
        headers={"Authorization": "Bearer test-key"},
    )
    return GrokClient(http_client)


def test_parses_each_cited_post_into_a_url_and_paraphrase():
    client = _fake_client(FIXTURE)
    posts = client.search_creator_posts("FPLGeneral")

    assert len(posts) == 5
    urls = {p["url"] for p in posts}
    assert "https://x.com/FPLGeneral/status/2089458687807217681" in urls
    assert "https://x.com/FPLGeneral/status/2088972536226644405" in urls


def test_paraphrase_text_excludes_markdown_and_citation_markup():
    client = _fake_client(FIXTURE)
    posts = client.search_creator_posts("FPLGeneral")

    by_url = {p["url"]: p["text"] for p in posts}
    haaland_post = by_url["https://x.com/FPLGeneral/status/2089319112396751250"]

    assert haaland_post == "Ranks Haaland #1 and Bruno #2 (likely for captaincy or points)."
    assert "[[" not in haaland_post
    assert "**" not in haaland_post
    assert "http" not in haaland_post


def test_no_citations_returns_an_empty_list():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "No results found.", "annotations": []}],
            }
        ]
    }
    client = _fake_client(payload)
    assert client.search_creator_posts("SomeoneQuiet") == []


def test_no_message_in_output_returns_an_empty_list():
    payload = {"output": [{"type": "reasoning", "summary": []}]}
    client = _fake_client(payload)
    assert client.search_creator_posts("SomeoneQuiet") == []


# ---------------------------------------------------------------------------
# Issue 30: the format that broke everything.

NUMBERED = json.loads(
    (Path(__file__).parent / "fixtures" / "xai_x_search_numbered_response.json").read_text()
)


def test_a_numbered_response_yields_one_post_per_item_not_per_url():
    """The bug this ticket exists for.

    `_BULLET_SPLIT` only matched "- " bullets, so a numbered response matched
    nothing, the fallback returned one item spanning the whole text, and every
    cited URL was paired with the ENTIRE blob. One Grok response became up to
    51 content rows holding identical text -- and each stance was then
    attributed to whichever URL happened to seed its row, which is the
    wrong-citation failure ADR-0014 exists to prevent."""
    posts = _fake_client(NUMBERED).search_creator_posts("LiveFPLnet")

    assert len(posts) == 3
    texts = [p["text"] for p in posts]
    # The point: three different paraphrases, not three copies of one blob.
    assert len(set(texts)) == 3
    assert not any(t.startswith("Here are the") for t in texts)


def test_a_numbered_item_keeps_only_the_paraphrase():
    posts = _fake_client(NUMBERED).search_creator_posts("LiveFPLnet")
    by_url = {p["url"]: p["text"] for p in posts}

    salah = by_url["https://x.com/LiveFPLnet/status/2044321987654321000"]
    assert salah.startswith("Argues Salah remains the safest captain")
    # No enumerator, no "Post URL:" header, no "Paraphrase:" label, no citation
    # markup, no url.
    assert not salah.startswith("3.")
    assert "Post URL" not in salah
    assert "Paraphrase" not in salah
    assert "http" not in salah
    assert "[[" not in salah


def test_an_unparseable_response_yields_one_post_not_one_per_url():
    """The fallback that caused the damage, inverted.

    When the structure is not recognised we cannot say which post a claim came
    from -- so we emit one coarse row rather than N rows each claiming to be a
    different post. A citation that is checkable and wrong is worse than one
    that is vague."""
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Salah looks strong and Haaland is the safer captain this week.",
                        "annotations": [
                            {"type": "url_citation", "url": "https://x.com/a/status/1",
                             "start_index": 5},
                            {"type": "url_citation", "url": "https://x.com/a/status/2",
                             "start_index": 30},
                        ],
                    }
                ],
            }
        ]
    }

    posts = _fake_client(payload).search_creator_posts("someone")

    assert len(posts) == 1
    assert posts[0]["url"] == "https://x.com/a/status/1"


def test_a_no_results_response_is_not_stored_as_content():
    """19 such rows were in the live database. They yielded no stances -- the
    extractor correctly found no opinions in them -- but they were embedded
    into the semantic-search corpus as though they were creator opinion."""
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "No matching posts found.**\n\nAfter searching the "
                            "@SolioAnalytics account, the account's posts focus elsewhere."
                        ),
                        "annotations": [
                            {"type": "url_citation",
                             "url": "https://x.com/i/status/2019330151813685430",
                             "start_index": 40}
                        ],
                    }
                ],
            }
        ]
    }

    assert _fake_client(payload).search_creator_posts("SolioAnalytics") == []
