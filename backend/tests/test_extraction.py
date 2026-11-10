import pytest

from fpl_coach.ingestion.extraction import (
    DEFAULT_EXTRACTION_MODEL,
    ClaudeStanceExtractor,
    FakeStanceExtractor,
)


def test_fake_stance_extractor_returns_canned_stances_and_records_the_call():
    canned = [{"player_name": "Haaland", "stance": "captain", "confidence": 0.9, "reason": "x"}]
    extractor = FakeStanceExtractor(canned)

    result = extractor.extract("some content")

    # Issue 16: the optional keys are always present, so the pipeline never has
    # to guess whether the model omitted one.
    assert result == [
        {
            "player_name": "Haaland",
            "subject": None,
            "stance": "captain",
            "confidence": 0.9,
            "reason": "x",
            "start_seconds": None,
        }
    ]
    assert extractor.calls == ["some content"]


def test_fake_stance_extractor_defaults_to_no_stances():
    extractor = FakeStanceExtractor()
    assert extractor.extract("anything") == []


def test_extraction_defaults_to_haiku_independently_of_the_reasoning_model(monkeypatch):
    """Issue 16: extraction is the one Claude call whose volume scales with the
    roster (a 6,000-word transcript per creator per day), so it must not
    inherit REASONING_MODEL the way it used to."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("REASONING_MODEL", "claude-opus-5")
    monkeypatch.delenv("EXTRACTION_MODEL", raising=False)

    assert ClaudeStanceExtractor()._model == DEFAULT_EXTRACTION_MODEL
    assert "haiku" in DEFAULT_EXTRACTION_MODEL


def test_the_extraction_model_can_still_be_overridden(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("EXTRACTION_MODEL", "claude-sonnet-5")

    assert ClaudeStanceExtractor()._model == "claude-sonnet-5"
    assert ClaudeStanceExtractor(model="claude-opus-5")._model == "claude-opus-5"


@pytest.mark.integration
def test_claude_extracts_a_buy_and_a_sell_stance_from_known_raw_text():
    """Pitfall #3 (ticket 06 design notes): a canned tool_use fake only
    proves plumbing, not that real extraction produces correct fields --
    this exercises the actual Claude-backed extractor against fixed input
    with a known expected shape."""
    content = (
        "Big fan of Ollie Watkins this week, buy him now before his price rises -- "
        "the underlying numbers are excellent. On the other hand I'd sell Kai Havertz, "
        "he's been a rotation risk all season."
    )

    stances = ClaudeStanceExtractor().extract(content)

    by_player = {s["player_name"].lower(): s for s in stances if s["player_name"]}
    watkins = next(v for k, v in by_player.items() if "watkins" in k)
    havertz = next(v for k, v in by_player.items() if "havertz" in k)

    assert watkins["stance"] == "buy"
    assert 0 <= watkins["confidence"] <= 1
    assert havertz["stance"] == "sell"
    assert 0 <= havertz["confidence"] <= 1


@pytest.mark.integration
def test_claude_returns_no_stances_for_content_with_no_player_opinion():
    content = "Really enjoyed today's podcast episode, great chat about the new season format."

    stances = ClaudeStanceExtractor().extract(content)

    assert stances == []


@pytest.mark.integration
def test_claude_extracts_the_non_player_claims_ticket_06_used_to_throw_away():
    """The claims a transcript is actually worth ingesting for. Under the
    ticket-06 schema (player_name NOT NULL, four-value enum) none of these
    could be stored at all, so extraction silently discarded them."""
    content = (
        "[00:15] I'm going to be playing my wildcard around gameweek 8, once the "
        "international break is out of the way. "
        "[02:40] Arsenal's fixture run from gameweek 6 onwards is the best in the game, "
        "so I want to be loading up on their assets before then. "
        "[05:10] I'd actively avoid Kai Havertz, he'll be rotated in the cups."
    )

    stances = ClaudeStanceExtractor().extract(content, timestamped=True)

    by_stance = {s["stance"] for s in stances}
    assert "chip_timing" in by_stance
    assert "fixture_view" in by_stance

    chip = next(s for s in stances if s["stance"] == "chip_timing")
    assert chip["player_name"] is None
    assert chip["subject"] is not None

    fixture = next(s for s in stances if s["stance"] == "fixture_view")
    assert fixture["player_name"] is None
    assert "arsenal" in (fixture["subject"] or "").lower()

    # Timestamps come back so ticket 17 can cite a moment, not just a video.
    assert any(s["start_seconds"] is not None for s in stances)
