import httpx
import pytest

from fpl_coach.ingestion.youtube_client import DEFAULT_BASE_URL, YouTubeClient

CHANNEL_ID = "UCxeOc7eFxq37yW_Nc-69deA"

# The real trap this client exists to avoid: the first "channelId" on a channel
# page can belong to a *recommended* channel, not this one. Only the feed link
# is reliably the channel's own, so the canned page puts a decoy first.
CHANNEL_PAGE = (
    '<html><head><meta name="x" content=\'{"channelId":"UCdecoyDECOYdecoyDECOYd"}\'>'
    '<link rel="alternate" type="application/rss+xml" '
    f'href="https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}">'
    "</head></html>"
)

# Deliberately NOT in date order, so the sort is actually proved.
FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Let's Talk FPL</title>
  <entry>
    <yt:videoId>older123456</yt:videoId>
    <title>FPL Gameweek 1 Preview</title>
    <published>2026-08-19T17:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>newer123456</yt:videoId>
    <title>FPL Gameweek 2 Preview</title>
    <published>2026-08-26T17:00:00+00:00</published>
  </entry>
</feed>
"""


def _client(handler) -> YouTubeClient:
    """Fakes at the HTTP boundary with httpx.MockTransport, the same
    convention as FPLClient/GrokClient."""
    return YouTubeClient(
        httpx.Client(
            base_url=DEFAULT_BASE_URL, transport=httpx.MockTransport(handler), follow_redirects=True
        )
    )


def test_resolves_a_channel_id_from_the_feed_link_not_a_recommended_channel():
    client = _client(lambda request: httpx.Response(200, text=CHANNEL_PAGE))

    assert client.resolve_channel_id("LetsTalkFPL") == CHANNEL_ID


def test_a_leading_at_sign_on_the_handle_is_accepted():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text=CHANNEL_PAGE)

    _client(handler).resolve_channel_id("@LetsTalkFPL")

    assert seen == ["/@LetsTalkFPL"]


def test_a_handle_that_does_not_exist_resolves_to_none_rather_than_raising():
    """Four of the twelve ticket-06 creators are data/analytics accounts with
    no channel at all. A roster with gaps must not abort an ingestion run."""
    client = _client(lambda request: httpx.Response(404, text="not found"))

    assert client.resolve_channel_id("NotAChannel") is None


def test_a_page_with_no_channel_id_resolves_to_none():
    client = _client(lambda request: httpx.Response(200, text="<html>nothing here</html>"))

    assert client.resolve_channel_id("Ambiguous") is None


def test_lists_recent_videos_newest_first_with_no_api_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=FEED_XML)

    videos = _client(handler).list_recent_videos(CHANNEL_ID)

    assert [v["video_id"] for v in videos] == ["newer123456", "older123456"]
    assert videos[0]["title"] == "FPL Gameweek 2 Preview"
    assert videos[0]["url"] == "https://www.youtube.com/watch?v=newer123456"
    assert videos[0]["published_at"] == "2026-08-26T17:00:00+00:00"
    # No key, no token, no quota -- the whole point of using the Atom feed
    # instead of the YouTube Data API.
    assert requests[0].url.path == "/feeds/videos.xml"
    assert requests[0].url.params["channel_id"] == CHANNEL_ID
    assert "authorization" not in {k.lower() for k in requests[0].headers}
    assert "key" not in requests[0].url.params


def test_an_empty_feed_lists_no_videos():
    empty = FEED_XML[: FEED_XML.index("<entry>")] + "</feed>"
    videos = _client(lambda request: httpx.Response(200, text=empty)).list_recent_videos(CHANNEL_ID)

    assert videos == []


@pytest.mark.integration
def test_real_youtube_resolves_a_handle_and_lists_videos():
    """The unit tests above prove parsing against canned XML. This proves the
    two live endpoints still behave as probed on 2026-08-27 -- YouTube can
    change either without warning, and both are unversioned."""
    client = YouTubeClient()

    channel_id = client.resolve_channel_id("LetsTalkFPL")
    assert channel_id == CHANNEL_ID

    videos = client.list_recent_videos(channel_id)
    assert len(videos) > 1
    assert all(v["video_id"] and v["published_at"] for v in videos)
    # The feed is capped at 15 with no paging, which is why ingestion has to
    # run at least weekly.
    assert len(videos) <= 15
