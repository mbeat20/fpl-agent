import os
import re

import httpx

DEFAULT_BASE_URL = "https://api.x.ai"
_MODEL = "grok-4"

# Splits the response's markdown output_text into per-POST items so each
# citation can be paired with the paraphrase around it.
#
# Issue 30: this used to be `\n(?=- )` -- bullets only. Grok returns a NUMBERED
# list ("1. **Post URL**: ..."), so the regex matched nothing, the fallback
# returned a single item spanning the whole response, and every cited URL was
# paired with the entire blob. One Grok response became up to 51 content rows
# holding identical text, each stance attributed to whichever URL happened to
# seed its row -- the wrong-citation failure ADR-0014 exists to prevent.
# Measured on live data before the fix: 295 chunks, 62 distinct texts.
_ITEM_SPLIT = re.compile(r"\n(?=\s*(?:[-*]\s|\d+[.)]\s))")

# A response that found nothing is not content. Grok says so in prose and
# still attaches citations to the accounts it searched, so "has annotations"
# is not enough to tell a result from a non-result.
_NO_RESULTS = re.compile(
    r"^\s*\**\s*(no matching posts|no relevant posts|no results|"
    r"i (?:was unable to|could not|couldn't) find)",
    re.IGNORECASE,
)

# Leading "1. " / "- " on a list item.
_ENUMERATOR = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*")
# A "**Post URL**: https://..." header line -- metadata, not anything the
# creator said.
_URL_HEADER = re.compile(r"^\s*\**\s*(?:post\s*)?url\s*\**\s*:", re.IGNORECASE)
# The "**Paraphrase**:" label in front of the text we actually want.
_PARAPHRASE_LABEL = re.compile(r"^\s*\**\s*paraphrase\s*\**\s*:\s*", re.IGNORECASE)


class GrokClient:
    """Wraps xAI's X Search tool (the Responses API's `x_search` tool type,
    docs.x.ai/developers/tools/x-search) to pull a creator's recent posts.

    Takes an httpx.Client so tests can fake it at its HTTP boundary
    (httpx.MockTransport with canned JSON) rather than mocking this class,
    same convention as FPLClient.

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
        if not annotations or _NO_RESULTS.match(text):
            continue

        items = _items_by_offset(text)
        if items is None:
            # Issue 30: the structure was not recognised. Emit ONE row for the
            # whole response rather than one per cited URL. Attributing the
            # entire blob to every URL is how 51 identical rows were created,
            # and a citation pointing at a post the claim did not come from is
            # worse than a coarse one -- it looks checkable and is wrong.
            first = annotations[0]["url"]
            if first not in seen_urls:
                posts.append({"url": first, "text": _clean(text)})
                seen_urls.add(first)
            continue

        for annotation in annotations:
            url = annotation["url"]
            if url in seen_urls:
                continue
            item = _item_containing(items, annotation["start_index"])
            if item is None:
                # A citation in the preamble rather than in any post item.
                # Dropped: there is no paraphrase to attach it to, and reaching
                # for the whole text is exactly the bug being fixed.
                continue
            posts.append({"url": url, "text": _clean(item)})
            seen_urls.add(url)
    return posts


def _items_by_offset(text: str) -> list[tuple[int, int, str]] | None:
    """(start, end, item_text) per list item, or None when the response has no
    list structure at all.

    None rather than "one item covering everything": the caller has to be able
    to tell "this is one post" from "I could not find the posts", because those
    want opposite behaviour and conflating them is issue 30.
    """
    starts = [m.start() for m in _ITEM_SPLIT.finditer("\n" + text)]
    if not starts:
        return None
    items = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text) + 1
        # -1 offsets for the leading "\n" prepended above.
        items.append((start - 1, end - 1, text[max(start - 1, 0):end - 1]))
    return items


def _item_containing(items: list[tuple[int, int, str]], offset: int) -> str | None:
    for start, end, item_text in items:
        if start <= offset < end:
            return item_text
    return None


_TRAILING_CITATION = re.compile(r"\[\[\d+\]\]\([^)]*\)\s*$")


def _clean(snippet: str) -> str:
    """One post item reduced to what the creator was actually paraphrased as
    saying -- no enumerator, no URL header, no citation markup.

    Handles both shapes Grok has been observed to return:

        - **<url>** (<date>)
          <paraphrase>

        1. **Post URL**: <url>
           **Paraphrase**: <paraphrase>
    """
    snippet = _TRAILING_CITATION.sub("", snippet)
    snippet = _ENUMERATOR.sub("", snippet, count=1).strip(" \n-*")

    # Drop leading metadata lines: a bare url, or a "Post URL:" header. Looped
    # because the numbered shape puts the enumerator and the header on the same
    # line, leaving the paraphrase on the next one.
    while "\n" in snippet:
        header, rest = snippet.split("\n", 1)
        stripped = header.strip(" \n-*")
        if stripped.startswith("http") or _URL_HEADER.match(header):
            snippet = rest.strip(" \n-*")
            continue
        break

    return _PARAPHRASE_LABEL.sub("", snippet).strip(" \n-*")
