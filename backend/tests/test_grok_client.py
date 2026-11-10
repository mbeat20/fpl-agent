import json
from pathlib import Path

import httpx

from fpl_coach.ingestion.grok_client import GrokClient

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "xai_x_search_response.json").read_text())


def _fake_client(payload: dict) -> GrokClient:
    """Fakes xAI's API at its HTTP boundary (httpx.MockTransport with canned
    JSON captured from a real call, 2026-08-18), same convention as
    FPLClient/UnderstatClient."""

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
