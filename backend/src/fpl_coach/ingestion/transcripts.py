import os
import time
from dataclasses import dataclass
from typing import Protocol

_DEFAULT_DELAY_SECONDS = 4.0


@dataclass(frozen=True)
class TranscriptSnippet:
    text: str
    start: float
    duration: float


@dataclass(frozen=True)
class Transcript:
    video_id: str
    snippets: list[TranscriptSnippet]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.snippets if s.text.strip())

    @property
    def duration_seconds(self) -> int:
        if not self.snippets:
            return 0
        last = self.snippets[-1]
        return int(last.start + last.duration)


class TranscriptBlocked(RuntimeError):
    """YouTube has rate-limited this IP, not this video.

    Distinct from "this video has no captions" because the correct responses
    are opposite: an unavailable transcript means move on to the next video,
    a block means stop fetching entirely, since every further request extends
    the ban.
    """


class TranscriptSource(Protocol):
    def fetch(self, video_id: str) -> Transcript | None: ...


class YouTubeTranscriptSource:
    """Wraps `youtube-transcript-api` behind a narrow protocol, the same shape
    as StanceExtractor, because it is not an httpx client and so cannot use
    the transport-faking convention the other ingestion clients share.

    Do NOT replace this with a hand-rolled fetch of the `captionTracks`
    `baseUrl` from the watch page. That URL returns HTTP 200 with an *empty
    body* under YouTube's proof-of-origin hardening, which fails silently and
    looks exactly like "this video has no captions".

    Rate limiting is not optional (measured 2026-08-27): 24 concurrent
    transcript fetches got this machine's IP blocked outright, and the block
    did not lift within minutes. Fetches are therefore serialised with a
    delay between them, and a block raises TranscriptBlocked so the caller
    stops rather than digging the hole deeper. For volume beyond one run a
    day, set YOUTUBE_PROXY_URL -- the library supports proxying precisely
    because of this ban.
    """

    def __init__(
        self,
        delay_seconds: float = _DEFAULT_DELAY_SECONDS,
        proxy_url: str | None = None,
        sleep=time.sleep,
    ) -> None:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api.proxies import GenericProxyConfig

        proxy_url = proxy_url if proxy_url is not None else os.environ.get("YOUTUBE_PROXY_URL")
        proxy_config = (
            GenericProxyConfig(http_url=proxy_url, https_url=proxy_url) if proxy_url else None
        )
        self._api = YouTubeTranscriptApi(proxy_config=proxy_config)
        self._delay_seconds = delay_seconds
        self._sleep = sleep
        self._fetched_any = False

    def fetch(self, video_id: str) -> Transcript | None:
        from youtube_transcript_api import _errors

        if self._fetched_any and self._delay_seconds:
            self._sleep(self._delay_seconds)
        self._fetched_any = True

        try:
            fetched = self._api.fetch(video_id)
        except _errors.RequestBlocked as exc:
            # IpBlocked subclasses RequestBlocked, and both must be caught
            # before CouldNotRetrieveTranscript below, which is their base.
            raise TranscriptBlocked(str(exc).splitlines()[0]) from exc
        except _errors.CouldNotRetrieveTranscript:
            # No captions, captions disabled, video unavailable/private. All
            # mean the same thing to ingestion: nothing to store here.
            return None

        return Transcript(
            video_id=video_id,
            snippets=[
                TranscriptSnippet(text=s.text, start=float(s.start), duration=float(s.duration))
                for s in fetched.snippets
            ],
        )


class FakeTranscriptSource:
    """Deterministic test double, faked at the same seam as
    FakeStanceExtractor. `blocked_after` reproduces the IP-ban path without
    touching the network."""

    def __init__(
        self,
        transcripts: dict[str, list[tuple[str, float, float]]] | None = None,
        blocked_after: int | None = None,
    ) -> None:
        self._transcripts = transcripts or {}
        self._blocked_after = blocked_after
        self.calls: list[str] = []

    def fetch(self, video_id: str) -> Transcript | None:
        if self._blocked_after is not None and len(self.calls) >= self._blocked_after:
            self.calls.append(video_id)
            raise TranscriptBlocked("faked IP block")
        self.calls.append(video_id)
        snippets = self._transcripts.get(video_id)
        if snippets is None:
            return None
        return Transcript(
            video_id=video_id,
            snippets=[TranscriptSnippet(text=t, start=s, duration=d) for t, s, d in snippets],
        )
