import os
import re

import httpx

DEFAULT_BASE_URL = "https://api.x.ai"
_MODEL = "grok-4"

# Splits the response's markdown output_text into per-bullet chunks so each
# citation (one per cited post) can be paired with the paraphrase around it.
_BULLET_SPLIT = re.compile(r"\n(?=- )")


class GrokClient:
    """Wraps xAI's X Search tool (the Responses API's `x_search` tool type,
    docs.x.ai/developers/tools/x-search) to pull a creator's recent posts.

    Takes an httpx.Client so tests can fake it at its HTTP boundary
    (httpx.MockTransport with canned JSON) rather than mocking this class,
    same convention as FPLClient/UnderstatClient.

    Confirmed against a real call (2026-08-18): the tool does NOT return
    verbatim post text. It returns Grok's own synthesized paraphrase of each
    cited post plus that post's URL as a `url_citation` annotation.
    search_creator_posts's `text` is therefore that paraphrase, not a raw
    tweet body -- see the same note on creator_content.content_text in
    schema.sql.
    """

    def __init__(self, http_client: httpx.Client | None = None, api_key: str | None = None) -> None:
        self._client = http_client or httpx.Client(
            base_url=DEFAULT_BASE_URL,
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key or os.environ['XAI_API_KEY']}"},
        )

    def search_creator_posts(self, x_handle: str, limit: int = 5) -> list[dict]:
        """Up to `limit` of the creator's most recent posts about FPL players
        (transfers, captaincy, buy/sell/hold), each as {"url": str, "text":
        str}. Empty list if the search surfaced nothing (e.g. a quiet
        account, or off-season -- see the first probe response captured
        2026-08-18)."""
        response = self._client.post(
            "/v1/responses",
            json={
                "model": _MODEL,
                "input": [
                    {
                        "role": "user",
                        "content": (
                            f"List the {limit} most recent distinct posts from the X "
                            f"account @{x_handle} about specific FPL players (transfers, "
                            "captaincy, buy/sell/hold). For each, give the post URL and a "
                            "short paraphrase of what it says."
                        ),
                    }
                ],
                "tools": [{"type": "x_search", "allowed_x_handles": [x_handle]}],
            },
        )
        response.raise_for_status()
        return _parse_posts(response.json())


def _parse_posts(payload: dict) -> list[dict]:
    message = next((item for item in payload.get("output", []) if item.get("type") == "message"), None)
    if message is None:
        return []

    posts: list[dict] = []
    seen_urls: set[str] = set()
    for content in message.get("content", []):
        text = content.get("text", "")
        annotations = [a for a in content.get("annotations", []) if a.get("type") == "url_citation"]
        if not annotations:
            continue

        bullets = _bullets_by_offset(text)
        for annotation in annotations:
            url = annotation["url"]
            if url in seen_urls:
                continue
            bullet = _bullet_containing(bullets, annotation["start_index"])
            snippet = _clean(bullet) if bullet is not None else _clean(text)
            posts.append({"url": url, "text": snippet})
            seen_urls.add(url)
    return posts


def _bullets_by_offset(text: str) -> list[tuple[int, int, str]]:
    """Returns (start, end, bullet_text) for each top-level "- " bullet."""
    starts = [m.start() for m in _BULLET_SPLIT.finditer("\n" + text)]
    if not starts:
        return [(0, len(text), text)]
    bullets = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text) + 1
        # +1 offsets for the leading "\n" prepended above.
        bullets.append((start - 1, end - 1, text[max(start - 1, 0):end - 1]))
    return bullets


def _bullet_containing(bullets: list[tuple[int, int, str]], offset: int) -> str | None:
    for start, end, bullet_text in bullets:
        if start <= offset < end:
            return bullet_text
    return None


_TRAILING_CITATION = re.compile(r"\[\[\d+\]\]\([^)]*\)\s*$")


def _clean(snippet: str) -> str:
    snippet = _TRAILING_CITATION.sub("", snippet).strip(" \n-*")
    # Bullets are formatted "**<url>** (<date>)\n  <paraphrase>" -- drop that
    # header line once the trailing citation link is gone, keeping just the
    # paraphrase itself.
    if "\n" in snippet:
        header, rest = snippet.split("\n", 1)
        if header.strip().startswith("http"):
            snippet = rest.strip(" \n-*")
    return snippet
