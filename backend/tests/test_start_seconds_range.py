"""Ticket 17: a video citation's `start_seconds` must be a real position in
that video. Pure -- the boundary check takes the value and the duration.
"""

import pytest

from fpl_coach.ingestion.creator_pipeline import in_range_start_seconds

VIDEO = 7035  # a real duration from the corpus: 1h57m


class TestAccepts:
    def test_a_position_inside_the_video(self):
        assert in_range_start_seconds(1892, VIDEO) == 1892

    def test_the_very_last_second(self):
        assert in_range_start_seconds(VIDEO, VIDEO) == VIDEO

    def test_zero(self):
        """0 is in range and is stored as 0 -- this check is about whether a
        second exists in the content, not about whether it is worth linking
        to. `deep_link` separately declines to build a `?t=0s`, which says
        nothing the bare url does not."""
        assert in_range_start_seconds(0, VIDEO) == 0

    def test_no_duration_to_check_against(self):
        """A text post has no duration; nothing is knowable, so nothing is
        rejected."""
        assert in_range_start_seconds(1892, None) == 1892


class TestRejects:
    def test_past_the_end_of_the_video(self):
        """The live failure: `[31:32]` read as 31 hours 32 minutes. 162 of
        1,446 deep links pointed past their own video this way."""
        assert in_range_start_seconds(113520, VIDEO) is None

    def test_one_second_past_the_end(self):
        assert in_range_start_seconds(VIDEO + 1, VIDEO) is None

    def test_negative(self):
        assert in_range_start_seconds(-30, VIDEO) is None

    def test_absent(self):
        assert in_range_start_seconds(None, VIDEO) is None

    @pytest.mark.parametrize("junk", ["", "twelve", {}, [], "12:30"])
    def test_not_a_number(self, junk):
        """A tool schema's `"type": "integer"` is a request, not a guarantee
        -- the same lesson `STANCE_VOCABULARY` learned when the extractor
        returned `blank`."""
        assert in_range_start_seconds(junk, VIDEO) is None

    def test_a_numeric_string_is_still_read(self):
        """Rejecting junk must not reject a well-formed value that merely
        arrived as a string."""
        assert in_range_start_seconds("1892", VIDEO) == 1892


def test_an_out_of_range_value_is_not_repaired_into_a_plausible_one():
    """113,520 unpacks to 31h32m, and 31*60+32 = 1,892 would land neatly
    inside the video. Deliberately not done: it guesses which mistake the
    model made, and a citation pointing confidently at the wrong sentence is
    worse than one that opens the video at the start."""
    assert in_range_start_seconds(113520, VIDEO) is None
