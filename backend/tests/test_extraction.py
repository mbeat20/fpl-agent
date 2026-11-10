import pytest

from fpl_coach.ingestion.extraction import ClaudeStanceExtractor, FakeStanceExtractor


def test_fake_stance_extractor_returns_canned_stances_and_records_the_call():
    canned = [{"player_name": "Haaland", "stance": "captain", "confidence": 0.9, "reason": "x"}]
    extractor = FakeStanceExtractor(canned)

    result = extractor.extract("some content")

    assert result == canned
    assert extractor.calls == ["some content"]


def test_fake_stance_extractor_defaults_to_no_stances():
    extractor = FakeStanceExtractor()
    assert extractor.extract("anything") == []


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

    by_player = {s["player_name"].lower(): s for s in stances}
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
