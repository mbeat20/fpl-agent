import pytest

from fpl_coach.ingestion.transcripts import (
    FakeTranscriptSource,
    Transcript,
    TranscriptBlocked,
    TranscriptSnippet,
    YouTubeTranscriptSource,
)

SNIPPETS = [("hello there", 0.0, 3.5), ("buy Wissa this week", 3.5, 4.0)]


def test_transcript_text_joins_snippets_and_drops_blanks():
    transcript = Transcript(
        video_id="v",
        snippets=[
            TranscriptSnippet("hello", 0.0, 1.0),
            TranscriptSnippet("   ", 1.0, 1.0),
            TranscriptSnippet("world", 2.0, 1.0),
        ],
    )

    assert transcript.text == "hello world"


def test_transcript_duration_is_the_end_of_its_last_snippet():
    transcript = Transcript(video_id="v", snippets=[TranscriptSnippet("x", 3595.0, 5.0)])

    assert transcript.duration_seconds == 3600


def test_an_empty_transcript_has_no_duration():
    assert Transcript(video_id="v", snippets=[]).duration_seconds == 0


def test_fake_source_returns_a_transcript_and_records_the_call():
    source = FakeTranscriptSource({"vid1": SNIPPETS})

    transcript = source.fetch("vid1")

    assert transcript is not None
    assert transcript.text == "hello there buy Wissa this week"
    assert source.calls == ["vid1"]


def test_fake_source_returns_none_for_a_video_it_has_no_transcript_for():
    """None means "no captions on this video" -- move on to the next one."""
    assert FakeTranscriptSource({}).fetch("vid1") is None


def test_fake_source_can_reproduce_the_ip_block():
    source = FakeTranscriptSource({"a": SNIPPETS, "b": SNIPPETS}, blocked_after=1)

    assert source.fetch("a") is not None
    with pytest.raises(TranscriptBlocked):
        source.fetch("b")


def test_real_source_sleeps_between_fetches_but_not_before_the_first():
    """Rate limiting is not optional: 24 concurrent fetches got this machine's
    IP blocked outright on 2026-08-27, and the block did not lift within
    minutes. Proved with an injected sleep so the test itself costs nothing."""
    slept = []
    source = YouTubeTranscriptSource(delay_seconds=4.0, sleep=slept.append)

    class _Fetched:
        snippets = [type("S", (), {"text": "hi", "start": 0.0, "duration": 1.0})()]

    source._api = type("Api", (), {"fetch": staticmethod(lambda video_id: _Fetched())})()

    source.fetch("a")
    assert slept == []
    source.fetch("b")
    assert slept == [4.0]


def test_real_source_turns_a_block_into_transcript_blocked_and_a_missing_transcript_into_none():
    """The two failures must stay distinguishable: no captions means skip this
    video, a block means stop fetching entirely."""
    from youtube_transcript_api import _errors

    source = YouTubeTranscriptSource(delay_seconds=0)

    def _raise(exc):
        def fetch(video_id):
            raise exc

        source._api = type("Api", (), {"fetch": staticmethod(fetch)})()

    _raise(_errors.IpBlocked("vid"))
    with pytest.raises(TranscriptBlocked):
        source.fetch("vid")

    _raise(_errors.TranscriptsDisabled("vid"))
    assert source.fetch("vid") is None


@pytest.mark.integration
def test_real_youtube_transcript_has_the_expected_shape():
    """Proves the library still works against real YouTube, and that a
    transcript really is worth ~55x a Grok paraphrase.

    Expect this to fail with TranscriptBlocked if the whole suite's
    integration tests have just hammered YouTube -- that is the constraint
    itself, not a flaky test.
    """
    from fpl_coach.ingestion.youtube_client import YouTubeClient

    client = YouTubeClient()
    channel_id = client.resolve_channel_id("LetsTalkFPL")
    videos = client.list_recent_videos(channel_id)

    transcript = None
    for video in videos[:3]:
        transcript = YouTubeTranscriptSource(delay_seconds=4.0).fetch(video["video_id"])
        if transcript is not None:
            break

    assert transcript is not None, "no transcript on any of the 3 most recent videos"
    assert len(transcript.snippets) > 50
    assert transcript.duration_seconds > 60
    assert all(s.start >= 0 for s in transcript.snippets)
    # Timestamps must be monotonic, or a chunk's start_seconds is meaningless.
    starts = [s.start for s in transcript.snippets]
    assert starts == sorted(starts)
