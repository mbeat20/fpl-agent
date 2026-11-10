import re
from xml.etree import ElementTree

import httpx

DEFAULT_BASE_URL = "https://www.youtube.com"

# A channel page embeds its own feed URL in a <link rel="alternate"> tag, and
# that is the only channel id on the page guaranteed to be the channel's own.
# Matching a bare "channelId":"UC..." instead is wrong: the first such match
# can belong to a recommended or related channel, which resolved
# @LetsTalkFPL to a completely different channel during the ticket-16 probe.
_FEED_LINK_RE = re.compile(r"channel_id=(UC[\w-]{22})")
_CANONICAL_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

# YouTube serves the channel page differently (or not at all) without a
# browser User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class YouTubeClient:
    """Lists a channel's recent videos with no YouTube Data API key and no
    quota, via the public Atom feed at /feeds/videos.xml.

    Takes an httpx.Client so tests fake it at its HTTP boundary with canned
    XML rather than mocking this class, the same convention as
    FPLClient/GrokClient.

    Probed live 2026-08-27:
      * `/@<handle>` -> HTTP 200 with the channel id in the page. The handle
        is NOT always the X handle -- @LetsTalk_FPL on X is @LetsTalkFPL on
        YouTube -- so the resolved id is stored once rather than re-derived
        per run.
      * `/feeds/videos.xml?channel_id=UC...` -> the 15 most recent videos.
        The older `?user=<name>` form is dead (404).
      * The feed is capped at 15 entries with no paging, which is the real
        reason ingestion has to run at least weekly to avoid losing videos --
        not a choice this client can widen.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(
            base_url=DEFAULT_BASE_URL, timeout=30.0, headers=_HEADERS, follow_redirects=True
        )

    def resolve_channel_id(self, handle: str) -> str | None:
        """The UC... channel id for an @handle, or None when the handle does
        not exist. None rather than raising: four of the twelve ticket-06
        creators are data/analytics accounts with no channel at all, and a
        roster with gaps in it must not abort a whole ingestion run."""
        response = self._client.get(f"/@{handle.lstrip('@')}")
        if response.status_code != 200:
            return None
        match = _FEED_LINK_RE.search(response.text) or _CANONICAL_RE.search(response.text)
        return match.group(1) if match else None

    def list_recent_videos(self, channel_id: str) -> list[dict]:
        """The channel's recent videos, newest first, as
        {"video_id", "title", "published_at", "url"}.

        Sorted here rather than trusting feed order: the feed is usually
        newest-first but nothing documents that, and ticket 18's recency
        ranking depends on published_at being right.
        """
        response = self._client.get("/feeds/videos.xml", params={"channel_id": channel_id})
        response.raise_for_status()
        return _parse_feed(response.text)


def _parse_feed(xml: str) -> list[dict]:
    root = ElementTree.fromstring(xml)
    videos = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", namespaces=_NS)
        if not video_id:
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": (entry.findtext("atom:title", namespaces=_NS) or "").strip(),
                "published_at": entry.findtext("atom:published", namespaces=_NS),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    videos.sort(key=lambda v: v["published_at"] or "", reverse=True)
    return videos
